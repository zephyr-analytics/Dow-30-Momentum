"""Dow 30 momentum dashboard (DIA market filter). Run: python dow_dashboard.py -> http://127.0.0.1:8051
Needs momentum_dashboard.py in the same folder."""
import os
os.environ["DASH_INDEX"] = "dow"
import momentum_dashboard  # noqa: E402

if __name__ == "__main__":
    momentum_dashboard.run()
