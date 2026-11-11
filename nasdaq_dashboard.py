"""Nasdaq-100 momentum dashboard (QQQ market filter). Run: python nasdaq_dashboard.py -> http://127.0.0.1:8050
Needs momentum_dashboard.py in the same folder."""
import os
os.environ["DASH_INDEX"] = "nasdaq"
import momentum_dashboard  # noqa: E402

if __name__ == "__main__":
    momentum_dashboard.run()
