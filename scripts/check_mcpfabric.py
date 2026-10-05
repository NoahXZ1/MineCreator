"""只读检查本机 MCPFabric：游戏状态、玩家位置和脚下方块。"""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("本机接口出现重定向，已停止请求。")


class MCPFabricClient:
    def __init__(self, game_dir: Path, timeout: float = 15):
        config_path = game_dir / "config" / "mcpfabric.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if config.get("host") not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Stage 1 只连接绑定在 127.0.0.1 的本机接口。")
        port = config.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise RuntimeError("MCPFabric 配置中的端口无效。")
        token = config.get("token")
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("MCPFabric 配置中缺少连接 token。")
        self.url = f"http://127.0.0.1:{port}/rpc"
        self.token = token
        self.timeout = timeout
        # 本机请求绕过系统代理；认证信息只在内存中使用。
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def call(self, method: str, params: dict | None = None) -> dict:
        body = json.dumps({"method": method, "params": params or {}}).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                envelope = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{method}: HTTP {exc.code}，请检查接口与认证配置。") from None
        if envelope.get("ok") is not True:
            error = envelope.get("error", {})
            raise RuntimeError(
                f"{method}: {error.get('code', 'unknown')} — "
                f"{error.get('message', '接口调用失败')}"
            )
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"{method}: 接口返回的数据格式不符合预期。")
        return result


def check_connection(client: MCPFabricClient) -> dict:
    status = client.call("info.status")
    if not status.get("integratedServer"):
        raise RuntimeError("接口已连接，但尚未进入单人世界。请进入测试存档后再运行。")
    player = client.call("player.getState")
    position = {
        "x": math.floor(player["x"]),
        "y": math.floor(player["y"]) - 1,
        "z": math.floor(player["z"]),
        "dimension": player["dimension"],
    }
    block = client.call("world.getBlock", position)
    if not isinstance(block.get("id"), str):
        raise RuntimeError("方块回读结果中缺少 id。")
    return {
        "检查通过": True,
        "Minecraft": status["minecraftVersion"],
        "MCPFabric": status["modVersion"],
        "加载器": status["loader"],
        "单人世界已连接": status["integratedServer"],
        "玩家位置": {key: player[key] for key in ("x", "y", "z", "dimension", "gameMode")},
        "脚下方块": block,
        "世界写入接口可用": "world_write" in status.get("capabilities", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_dir = Path(os.environ["APPDATA"]) / ".minecraft" if "APPDATA" in os.environ else None
    parser.add_argument("--game-dir", type=Path, default=default_dir, help="Minecraft 游戏目录，默认使用 %%APPDATA%%\\.minecraft")
    args = parser.parse_args()
    if args.game_dir is None:
        parser.error("请通过 --game-dir 指定 Minecraft 游戏目录。")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = check_connection(MCPFabricClient(args.game_dir))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"检查未通过：{exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
