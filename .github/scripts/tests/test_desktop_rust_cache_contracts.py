"""桌面端构建的 Rust 编译缓存：main 上预热、构建（预构建 / tag 回退构建）只读，两边编译参数必须一致。"""
from pathlib import Path
import re

import pytest


WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
# 三平台 build job 定义在 reusable workflow 里，被 main 预构建和 tag 回退构建共用。
RELEASE = "build-desktop-packages.yml"
WARMUP = "desktop-rust-cache-warmup.yml"

# (发布 job, 预热 job, rust-cache shared-key, cargo 步骤名)
PLATFORMS = [
    ("build-windows-release", "warm-windows", "desktop-release-windows", "Build Rust API for Windows"),
    ("build-linux-release", "warm-linux", "desktop-release-linux", "Build Rust API for Linux"),
    ("build-macos-release", "warm-macos", "desktop-release-macos", "Build Rust API for macOS"),
]


def _source(workflow: str) -> str:
    return (WORKFLOWS / workflow).read_text(encoding="utf-8")


def _job(workflow: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:|\Z)", _source(workflow), re.M | re.S
    )
    assert match is not None, f"missing {name} in {workflow}"
    return match.group(1)


def _steps(job_text: str) -> dict[str, str]:
    """按步骤名切出每个步骤的原文（去掉步骤前的注释行）。"""
    steps: dict[str, str] = {}
    for chunk in re.split(r"^      - name: ", job_text, flags=re.M)[1:]:
        name, _, body = chunk.partition("\n")
        body = re.sub(r"^\s*#.*\n", "", body, flags=re.M)
        steps[name.strip()] = body.rstrip()
    return steps


def _step_names(job_text: str) -> list[str]:
    return [name.strip() for name in re.findall(r"^      - name: (.+)$", job_text, re.M)]


def _with_block(step: str) -> dict[str, str]:
    match = re.search(r"^        with:\n((?:          .+\n?)+)", step + "\n", re.M)
    if match is None:
        return {}
    return dict(
        (key.strip(), value.strip())
        for key, _, value in (line.partition(":") for line in match.group(1).splitlines())
    )


@pytest.mark.parametrize("release_job,warm_job,shared_key,cargo_step", PLATFORMS)
def test_warmup_builds_exactly_like_release(release_job, warm_job, shared_key, cargo_step):
    release = _steps(_job(RELEASE, release_job))
    warm = _steps(_job(WARMUP, warm_job))
    # cargo 命令（target / features / --locked / shell）逐字一致，否则缓存对不上。
    assert warm[cargo_step] == release[cargo_step]
    assert "cargo build --release --locked" in release[cargo_step]
    # 工具链安装（含 Windows 的 targets）一致：rustc 版本进缓存 key。
    assert warm["Setup Rust toolchain"] == release["Setup Rust toolchain"]
    # setup-python 会改 PATH / PKG_CONFIG_PATH 等，预热时同样装上，保持环境一致。
    assert (
        warm["Setup Python runtime for desktop bundle"]
        == release["Setup Python runtime for desktop bundle"]
    )


@pytest.mark.parametrize("release_job,warm_job,shared_key,cargo_step", PLATFORMS)
def test_release_reads_warmed_cache_without_writing(release_job, warm_job, shared_key, cargo_step):
    for workflow, job_name in ((RELEASE, release_job), (WARMUP, warm_job)):
        job_text = _job(workflow, job_name)
        names = _step_names(job_text)
        assert names.index("Setup Rust toolchain") < names.index("Rust Cache") < names.index(cargo_step)
        step = _steps(job_text)["Rust Cache"]
        assert "uses: Swatinem/rust-cache@v2" in step
        options = _with_block(step)
        assert options["shared-key"] == shared_key
        assert options["workspaces"] == '". -> target"'
        if workflow == RELEASE:
            # tag 写的缓存下一个 tag 读不到，发布只读。
            assert options["save-if"] == "false"
        else:
            assert "save-if" not in options


def test_cache_key_env_is_not_overridden():
    """rust-cache 把 CARGO* / RUST* / CC* 等环境变量算进 key，两边任一处单独加了就
    整条 key 对不上；要加就两边一起加并更新这条测试。"""
    for workflow in (RELEASE, WARMUP):
        assert re.search(
            r"^\s+(CARGO|RUST|CC|CFLAGS|CXX|CMAKE)\w*:", _source(workflow), re.M
        ) is None, workflow


def test_warmup_runs_on_schedule_and_manual_dispatch_only():
    """push main 不再预热：发布只出安装包和源码包，推送时不该额外跑构建。

    预热只剩每周定时（保住缓存不被 7 天淘汰）和手动触发。
    """
    source = _source(WARMUP)
    on_block = re.search(r'^"on":\n(.*?)(?=^\S)', source, re.M | re.S).group(1)
    assert "push:" not in on_block, "push 不该再触发预热"
    assert "pull_request" not in on_block
    assert "schedule:" in on_block
    assert "workflow_dispatch:" in on_block
    # 预热只编译，不需要任何写权限或 secret。
    assert "contents: read" in source
    assert "secrets." not in source
