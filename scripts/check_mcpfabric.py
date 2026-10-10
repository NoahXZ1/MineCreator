"""Read-only local MCPFabric check: game status, player position, and support block."""

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
        raise RuntimeError("The local API redirected the request; stopped.")


class MCPFabricClient:
    def __init__(self, game_dir: Path, timeout: float = 15):
        config_path = game_dir / "config" / "mcpfabric.config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise ValueError('MCPFabric configuration was not found. Check the game directory in Settings and launch Minecraft with the mod once.') from None
        except (ValueError, OSError):
            raise ValueError('MCPFabric configuration could not be read. Check the selected game directory and mod configuration.') from None
        if config.get("host") not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Stage 1 connects only to a localhost API.")
        port = config.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise RuntimeError("Invalid port in the MCPFabric configuration.")
        token = config.get("token")
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("Missing token in the MCPFabric configuration.")
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
            raise RuntimeError(f"{method}: HTTP {exc.code}; check API and authentication settings.") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            raise RuntimeError('Minecraft MCPFabric is unreachable. Open the game with the mod, enter a single-player world, and check the game directory in Settings.') from None
        if envelope.get("ok") is not True:
            error = envelope.get("error", {})
            raise RuntimeError(
                f"{method}: {error.get('code', 'unknown')} — "
                f"{error.get('message', 'API call failed')}"
            )
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"{method}: unexpected API response format.")
        return result


def check_connection(client: MCPFabricClient) -> dict:
    status = client.call("info.status")
    if not status.get("integratedServer"):
        raise RuntimeError("API connected, but no single-player world is open. Enter the test world first.")
    player = client.call("player.getState")
    position = {
        "x": math.floor(player["x"]),
        "y": math.floor(player["y"]) - 1,
        "z": math.floor(player["z"]),
        "dimension": player["dimension"],
    }
    block = client.call("world.getBlock", position)
    if not isinstance(block.get("id"), str):
        raise RuntimeError("Block readback is missing its ID.")
    return {
        "check_passed": True,
        "Minecraft": status["minecraftVersion"],
        "MCPFabric": status["modVersion"],
        "loader": status["loader"],
        "single_player_connected": status["integratedServer"],
        "player_position": {key: player[key] for key in ("x", "y", "z", "dimension", "gameMode")},
        "support_block": block,
        "world_write_available": "world_write" in status.get("capabilities", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_dir = Path(os.environ["APPDATA"]) / ".minecraft" if "APPDATA" in os.environ else None
    parser.add_argument("--game-dir", type=Path, default=default_dir, help="Minecraft game directory; defaults to %%APPDATA%%\\.minecraft")
    args = parser.parse_args()
    if args.game_dir is None:
        parser.error("Specify the Minecraft game directory with --game-dir.")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = check_connection(MCPFabricClient(args.game_dir))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"Connection check failed: {exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
