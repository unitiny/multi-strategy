import os
from pathlib import Path
from dotenv import load_dotenv
import yaml


def load_config(config_path: str = None) -> dict:
    base_dir = Path(__file__).resolve().parent.parent
    load_dotenv(base_dir / ".env")

    if config_path is None:
        config_path = base_dir / "config.yaml"
    else:
        config_path = Path(config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["binance"]["api_key"] = os.getenv("BINANCE_API_KEY", "")
    config["binance"]["api_secret"] = os.getenv("BINANCE_API_SECRET", "")
    config["dingtalk"] = {
        "webhook": os.getenv("DINGTALK_WEBHOOK", ""),
        "secret": os.getenv("DINGTALK_SECRET", ""),
    }

    proxy_http = os.getenv("PROXY_HTTP")
    proxy_https = os.getenv("PROXY_HTTPS")
    if proxy_http or proxy_https:
        config["binance"]["proxy"] = {
            "http": proxy_http or "",
            "https": proxy_https or "",
        }
    else:
        config["binance"].pop("proxy", None)

    return config


def get_symbol_params(config: dict, symbol: str) -> dict:
    base = dict(config["strategy"])
    for item in config.get("watchlist", []):
        if item["symbol"] == symbol:
            for k, v in item.items():
                if k != "symbol":
                    base[k] = v
            break
    return base
