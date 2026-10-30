from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_ROOT.parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT))

from resolve_release_version import resolve_release_version


@pytest.mark.parametrize(
    ("tag", "version", "prerelease", "stable"),
    [
        ("4.2.0-beta1", "4.2.0-beta1", "true", "false"),
        ("v4.2.0-beta.1+build.7", "4.2.0-beta.1+build.7", "true", "false"),
        ("4.2.0", "4.2.0", "false", "true"),
        ("v4.2.0", "4.2.0", "false", "true"),
    ],
)
def test_resolves_release_tags(
    tag: str,
    version: str,
    prerelease: str,
    stable: str,
) -> None:
    assert resolve_release_version(tag) == {
        "tag": tag,
        "version": version,
        "prerelease": prerelease,
        "stable": stable,
    }


@pytest.mark.parametrize("tag", ["", "release/4.2.0", "4.2", "04.2.0", "4.2.0 beta"])
def test_rejects_invalid_release_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="invalid release reference"):
        resolve_release_version(tag)


def test_manual_docker_build_can_use_safe_label_without_marking_it_stable() -> None:
    assert resolve_release_version("dev", allow_label=True) == {
        "tag": "dev",
        "version": "dev",
        "prerelease": "true",
        "stable": "false",
    }


@pytest.mark.parametrize("tag", ["feature/test", "two words", "bad:tag", "../latest"])
def test_manual_docker_label_rejects_unsafe_values(tag: str) -> None:
    with pytest.raises(ValueError, match="invalid release reference"):
        resolve_release_version(tag, allow_label=True)


def test_desktop_release_accepts_prefixed_and_unprefixed_version_tags() -> None:
    """只有桌面端发布还在打 tag 时自动跑；Docker 发布改成手动触发了。"""
    workflow = (REPO_ROOT / ".github" / "workflows" / "release-desktop.yml").read_text(
        encoding="utf-8"
    )

    assert "v[0-9]*.[0-9]*.[0-9]*" in workflow
    assert "[0-9]*.[0-9]*.[0-9]*" in workflow
    # resolve 用 find_release_candidate.py 解析 tag（内部调用 resolve_release_version，
    # 非法 tag 直接失败），回退构建拿到的就是它解析出的版本。
    assert "find_release_candidate.py" in workflow
    assert '--tag "$GITHUB_REF_NAME"' in workflow


def test_desktop_release_is_published_once_after_all_platforms_finish() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "release-desktop.yml"
    ).read_text(encoding="utf-8")
    builds = (
        REPO_ROOT / ".github" / "workflows" / "build-desktop-packages.yml"
    ).read_text(encoding="utf-8")
    publish_action = (
        REPO_ROOT / ".github" / "actions" / "publish-desktop-release" / "action.yml"
    ).read_text(encoding="utf-8")

    # 版本号只在 resolve 里从 tag 解析一次（find_release_candidate 调用
    # resolve_release_version），三个 build job 校验传入的版本号。
    assert "find_release_candidate.py" in workflow
    assert builds.count("resolve_release_version.py") == 3
    assert 'tag = "v$version"' not in workflow
    # 回退与提升共用同一个发布 action，Release 只在那里创建。
    assert "softprops/action-gh-release" not in workflow
    assert workflow.count("uses: ./.github/actions/publish-desktop-release") == 2
    assert publish_action.count("uses: softprops/action-gh-release@v3") == 1
    assert "prerelease: ${{ inputs.prerelease == 'true' }}" in publish_action
    assert "publish-desktop-release:" in workflow
    assert "promote-desktop-release:" in workflow
    assert "- build\n" in workflow
    for job in ("build-windows-release", "build-linux-release", "build-macos-release"):
        assert f"  {job}:" in builds
    assert "/SHA256SUMS.txt" in publish_action
    assert "--sums-out" in publish_action


def test_docker_latest_is_promoted_only_after_candidates_are_verified() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "release-docker.yml"
    ).read_text(encoding="utf-8")
    action = (
        REPO_ROOT / ".github" / "actions" / "docker-candidates" / "action.yml"
    ).read_text(encoding="utf-8")

    assert "Verify both candidate images" in action
    assert "Promote complete Docker release" in action
    assert "STABLE_RELEASE: ${{ inputs.stable }}" in action
    assert 'if [ "$STABLE_RELEASE" = "true" ]; then' in action
    assert action.index("Verify both candidate images") < action.index(
        "Promote complete Docker release"
    )
    assert "stable: ${{ steps.release.outputs.stable }}" in workflow
    assert "stable: ${{ needs.resolve.outputs.stable }}" in workflow
    assert "startsWith(github.ref, 'refs/tags/v')" not in workflow
