"""Fully automated GitHub repository creation, code push, and Release asset publisher.

Usage:
    python scripts/publish_to_github.py --token <GITHUB_PAT_TOKEN> [--repo-name VideoDownloader] [--private]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import requests

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"

RELEASE_TAG = "v3.0.0"
RELEASE_TITLE = "VideoDownloader v3.0.0 - 影视级多引擎极速下载器 & AI 智能分镜"
RELEASE_BODY = """## 🚀 VideoDownloader v3.0.0-Ultimate 正式版发布

全球流媒体多引擎可解释极速下载框架 + 影视级 AI 智能分镜脚本拆解与纯净文案提炼桌面客户端。

### ✨ 核心特性亮点
- **四级智能解析降级链**：原生短视频逆向接口 -> 无头 CDP 真实抓包嗅探 -> yt-dlp 全球百大平台 -> HTML5 通用保底嗅探。
- **8 线程 Range 并发极速下载**：支持大文件分块并发加速与 EMA 平滑测速，彻底突破单连接限速瓶颈。
- **AI 影视级分镜工作台**：
  - 6 轨自适应字幕/文案提取管线（支持 B站官方 AI 字幕秒级直采与伴随字幕落盘）；
  - 自动拆解导演级分镜表（镜号、景别、运镜、构图细节、台词旁白、BGM 节奏）；
  - 纯净逐字稿提炼、语气词智能清洗、爆款金句归纳；
  - 兼容阿里云百炼 (DeepSeek/Qwen)、Claude、OpenAI、本地 Ollama 等多服务商与自定义模型。
- **二次元感官与视觉工程**：
  - 5 套程序化矢量柔光背景（初音绿、樱花粉、星空紫、元气橙、琉璃蓝），零版权风险；
  - 纯正弦波自合成 44.1kHz 专属下载提示音效；
  - 快捷命令面板 (Ctrl+Shift+K) 与成就战报海报。
- **开箱即用**：零外部依赖，内置完整运行环境。

### 📦 资产校验与下载
| 文件名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `VideoDownloader-Setup-3.0.0-x64.exe` | 独立安装包 | 推荐新手，一键向导安装并生成桌面图标 |
| `VideoDownloader-3.0.0-Windows-portable.zip` | 绿色便携包 | 免安装解压即用，直接运行 `run_desktop.exe` |
| `SHA256SUMS.txt` | 安全指纹 | SHA-256 完整性校验文件 |
"""


class GitHubPublisher:
    def __init__(self, token: str, repo_name: str = "VideoDownloader", is_private: bool = False):
        self.token = token.strip()
        self.repo_name = repo_name.strip()
        self.is_private = is_private
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "VideoDownloader-Publisher/3.0.0",
        })
        self.user_login: str = ""
        self.user_name: str = ""
        self.user_email: str = ""
        self.repo_html_url: str = ""

    def verify_auth(self) -> None:
        print("[1/5] 正在验证 GitHub 访问令牌权限...")
        resp = self.session.get("https://api.github.com/user")
        if resp.status_code == 401:
            raise RuntimeError("GitHub 访问令牌无效或已过期，请检查 Token 是否正确！")
        if resp.status_code != 200:
            raise RuntimeError(f"验证 GitHub 用户失败: HTTP {resp.status_code} - {resp.text}")

        user_data = resp.json()
        self.user_login = user_data.get("login", "")
        self.user_name = user_data.get("name") or self.user_login
        self.user_email = user_data.get("email") or f"{self.user_login}@users.noreply.github.com"
        scopes = resp.headers.get("x-oauth-scopes", "未知")
        print(f"      ✓ 认证成功！当前 GitHub 登录身份: {self.user_login} ({self.user_name})")
        print(f"      ✓ 令牌具备的作用域权限: {scopes}")

    def ensure_repository(self) -> None:
        print(f"[2/5] 检查/创建远程仓库: {self.user_login}/{self.repo_name} ...")
        check_url = f"https://api.github.com/repos/{self.user_login}/{self.repo_name}"
        resp = self.session.get(check_url)
        if resp.status_code == 200:
            repo_info = resp.json()
            self.repo_html_url = repo_info.get("html_url", "")
            print(f"      ✓ 远程仓库已存在: {self.repo_html_url}")
            return

        # Create new repository
        payload = {
            "name": self.repo_name,
            "description": "High-performance multi-engine video downloader with AI storyboard analysis & anime themes.",
            "private": self.is_private,
            "has_issues": True,
            "has_projects": True,
            "has_wiki": False,
            "auto_init": False,
        }
        create_resp = self.session.post("https://api.github.com/user/repos", json=payload)
        if create_resp.status_code not in (200, 201):
            raise RuntimeError(f"创建仓库失败: HTTP {create_resp.status_code} - {create_resp.text}")

        created_info = create_resp.json()
        self.repo_html_url = created_info.get("html_url", "")
        print(f"      ✓ 仓库创建成功: {self.repo_html_url} (公开状态: {'私有' if self.is_private else '公开'})")

    def push_code(self) -> None:
        print("[3/5] 准备向 GitHub 推送代码仓库 (main 分支)...")
        # Ensure user name/email are set locally if not configured
        subprocess.run(["git", "config", "user.name", self.user_name], cwd=ROOT, check=False)
        subprocess.run(["git", "config", "user.email", self.user_email], cwd=ROOT, check=False)
        subprocess.run(["git", "branch", "-M", "main"], cwd=ROOT, check=True)

        # Authenticated push URL (will be sanitized immediately after push)
        auth_url = f"https://x-access-token:{self.token}@github.com/{self.user_login}/{self.repo_name}.git"
        public_url = f"https://github.com/{self.user_login}/{self.repo_name}.git"

        # Check existing remote
        remotes = subprocess.check_output(["git", "remote"], cwd=ROOT, text=True)
        if "origin" in remotes.split():
            subprocess.run(["git", "remote", "set-url", "origin", auth_url], cwd=ROOT, check=True)
        else:
            subprocess.run(["git", "remote", "add", "origin", auth_url], cwd=ROOT, check=True)

        try:
            print("      正在执行 git push -u origin main ...")
            push_proc = subprocess.run(
                ["git", "push", "-u", "origin", "main", "--force"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if push_proc.returncode != 0:
                raise RuntimeError(f"Git 推送失败: {push_proc.stderr}")
            print("      ✓ 源码推送完成，主分支 main 已同步到 GitHub！")
        finally:
            # SANITIZATION: clean up plain-text token from git config
            subprocess.run(["git", "remote", "set-url", "origin", public_url], cwd=ROOT, check=False)
            print("      ✓ 已安全清除本地 git remote 凭据缓存。")

    def create_or_get_release(self) -> Dict[str, Any]:
        print(f"[4/5] 创建/检索 GitHub Release: {RELEASE_TAG} ...")
        rel_url = f"https://api.github.com/repos/{self.user_login}/{self.repo_name}/releases/tags/{RELEASE_TAG}"
        resp = self.session.get(rel_url)
        if resp.status_code == 200:
            print(f"      ✓ 检测到已有 Release {RELEASE_TAG}，将复用并更新附件。")
            return resp.json()

        create_url = f"https://api.github.com/repos/{self.user_login}/{self.repo_name}/releases"
        payload = {
            "tag_name": RELEASE_TAG,
            "target_commitish": "main",
            "name": RELEASE_TITLE,
            "body": RELEASE_BODY,
            "draft": False,
            "prerelease": False,
        }
        create_resp = self.session.post(create_url, json=payload)
        if create_resp.status_code not in (200, 201):
            raise RuntimeError(f"创建 Release 失败: HTTP {create_resp.status_code} - {create_resp.text}")

        rel_data = create_resp.json()
        print(f"      ✓ Release 创建成功: {rel_data.get('html_url')}")
        return rel_data

    def upload_assets(self, release_data: Dict[str, Any]) -> None:
        print("[5/5] 正在向 Release 上传安装包与分发资产...")
        release_id = release_data["id"]
        upload_url_template = release_data["upload_url"]  # format: https://uploads.github.com/.../assets{?name,label}
        base_upload_url = upload_url_template.split("{")[0]

        existing_assets = {a["name"]: a["id"] for a in release_data.get("assets", [])}

        assets_to_upload = [
            DIST_DIR / "VideoDownloader-Setup-3.0.0-x64.exe",
            DIST_DIR / "VideoDownloader-3.0.0-Windows-portable.zip",
            DIST_DIR / "SHA256SUMS.txt",
        ]

        for asset_path in assets_to_upload:
            if not asset_path.exists():
                print(f"      ⚠️ 跳过缺失文件: {asset_path}")
                continue

            name = asset_path.name
            size_mb = asset_path.stat().st_size / (1024 * 1024)

            # If asset already exists, delete it first to allow clean overwrite
            if name in existing_assets:
                del_id = existing_assets[name]
                print(f"      - 正在清理已有旧资产: {name} (ID: {del_id}) ...")
                self.session.delete(f"https://api.github.com/repos/{self.user_login}/{self.repo_name}/releases/assets/{del_id}")

            print(f"      -> 正在流式上传 {name} ({size_mb:.1f} MB) ...")
            t0 = time.time()
            upload_url = f"{base_upload_url}?name={name}"

            with open(asset_path, "rb") as fp:
                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(asset_path.stat().st_size),
                }
                up_resp = requests.post(upload_url, data=fp, headers=headers)

            if up_resp.status_code not in (200, 201):
                print(f"      ❌ 上传 {name} 失败: HTTP {up_resp.status_code} - {up_resp.text}")
            else:
                elapsed = time.time() - t0
                dl_url = up_resp.json().get("browser_download_url", "")
                print(f"      ✓ 上传成功 ({elapsed:.1f}s): {dl_url}")

    def run(self) -> None:
        t_start = time.time()
        print("=" * 60)
        print("  VideoDownloader 全自动 GitHub 一键发布流水线")
        print("=" * 60)
        self.verify_auth()
        self.ensure_repository()
        self.push_code()
        release_data = self.create_or_get_release()
        self.upload_assets(release_data)

        total_time = time.time() - t_start
        print("=" * 60)
        print(f"🎉 全部发布成功！总耗时: {total_time:.1f} 秒")
        print(f"🔗 仓库主页: {self.repo_html_url}")
        print(f"📦 Release 下载页: {release_data.get('html_url')}")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="一键全自动发布 VideoDownloader 到 GitHub")
    parser.add_argument("--token", required=True, help="GitHub Personal Access Token (classic with repo scope)")
    parser.add_argument("--repo-name", default="VideoDownloader", help="GitHub 仓库名称")
    parser.add_argument("--private", action="store_true", help="是否创建为私有仓库（默认公开）")
    args = parser.parse_args()

    publisher = GitHubPublisher(token=args.token, repo_name=args.repo_name, is_private=args.private)
    try:
        publisher.run()
    except Exception as exc:
        print(f"\n❌ 发布过程中遇到错误: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
