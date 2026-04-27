import os
import re
from datetime import datetime, timedelta

import pandas as pd
from stockstats import wrap

from .base import BaseMarketDataProvider
from ..trade_calendar import cn_no_data_reason


class CnTushareProvider(BaseMarketDataProvider):
    """A-share provider backed by Tushare Pro (https://tushare.pro).

    Requires a valid API token via the ``TA_TUSHARE_TOKEN`` environment variable.
    Raises ``NotImplementedError`` when the token is missing or calls fail,
    allowing the provider routing system to fall back to the next vendor.
    """

    INDICATOR_DESCRIPTIONS = {
        "close_50_sma": "50 日均线（SMA）：中期趋势指标。",
        "close_200_sma": "200 日均线（SMA）：长期趋势基准。",
        "close_10_ema": "10 日指数均线（EMA）：短期响应更快。",
        "macd": "MACD：趋势与动量综合指标。",
        "macds": "MACD 信号线（Signal）。",
        "macdh": "MACD 柱状图（Histogram）。",
        "rsi": "RSI：衡量超买/超卖的动量指标。",
        "boll": "布林中轨（20 日均线）。",
        "boll_ub": "布林上轨。",
        "boll_lb": "布林下轨。",
        "atr": "ATR：真实波动幅度均值，用于波动与风控。",
        "vwma": "VWMA：成交量加权均线。",
        "mfi": "MFI：资金流量指标。",
    }

    def __init__(self):
        self._token = os.getenv("TA_TUSHARE_TOKEN", "").strip()

    @property
    def name(self) -> str:
        return "cn_tushare"

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _ts(self):
        """Lazy-import and initialize Tushare Pro API."""
        if not self._token:
            raise NotImplementedError(
                "cn_tushare requires TA_TUSHARE_TOKEN environment variable. "
                "Register at https://tushare.pro to obtain one."
            )
        try:
            import tushare as ts  # type: ignore
        except ImportError as exc:
            raise NotImplementedError(
                "cn_tushare requires 'tushare'. Install it with: pip install tushare"
            ) from exc
        ts.set_token(self._token)
        return ts.pro_api()

    @staticmethod
    def _normalize_symbol(symbol: str) -> tuple[str, str]:
        """Return (ts_code, market_prefix).

        Tushare uses ``XXXXXX.SH`` / ``XXXXXX.SZ`` format.
        """
        s = symbol.strip().upper()
        m = re.search(r"(\d{6})", s)
        if not m:
            raise NotImplementedError(
                f"cn_tushare only supports A-share 6-digit symbols, got: {symbol}"
            )
        code = m.group(1)
        exchange = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        return f"{code}.{exchange}", exchange.lower()

    @staticmethod
    def _normalize_ts_code(symbol: str) -> str:
        """Accept both ``600519`` and ``600519.SH`` and return Tushare ts_code."""
        s = symbol.strip().upper()
        m = re.search(r"(\d{6})", s)
        if not m:
            raise NotImplementedError(
                f"cn_tushare only supports A-share 6-digit symbols, got: {symbol}"
            )
        code = m.group(1)
        if ".SH" in s or code.startswith(("5", "6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    @staticmethod
    def _slice_hist_df(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        start_dt = pd.to_datetime(start_date, errors="coerce")
        end_dt = pd.to_datetime(end_date, errors="coerce")
        if pd.isna(start_dt) or pd.isna(end_dt):
            return df
        out = df.copy()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = out.dropna(subset=["Date"])
        out = out[(out["Date"] >= start_dt) & (out["Date"] <= end_dt)]
        return out.sort_values("Date").reset_index(drop=True)

    @staticmethod
    def _normalize_hist_df(raw_df: pd.DataFrame) -> pd.DataFrame:
        """Convert Tushare daily data to unified OHLCV format."""
        if raw_df is None or raw_df.empty:
            return pd.DataFrame()
        # Tushare columns: trade_date, open, high, low, close, vol, amount, ...
        rename = {
            "trade_date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "vol": "Volume",
            "pct_chg": "PctChg",
            "amount": "Amount",
        }
        df = raw_df.rename(columns=rename).copy()
        required = ["Date", "Open", "High", "Low", "Close", "Volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Tushare hist dataframe missing columns: {missing}")
        df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date")
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    @staticmethod
    def _format_hist_csv(df: pd.DataFrame, symbol: str, start: str, end: str) -> str:
        if df is None or df.empty:
            return f"No data found for symbol '{symbol}' between {start} and {end}"
        out = df.copy()
        out["Dividends"] = 0.0
        out["Stock Splits"] = 0.0
        out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")
        header = f"# Stock data for {symbol} from {start} to {end}\n"
        header += f"# Total records: {len(out)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + out.to_csv(index=False)

    @staticmethod
    def _shrink_table(df: pd.DataFrame, max_rows: int = 12, max_cols: int = 16) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        rows = min(max_rows, len(df))
        cols = min(max_cols, len(df.columns))
        return df.head(rows).iloc[:, :cols]

    # ── BaseMarketDataProvider implementations ───────────────────────────────

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        ts_code = self._normalize_ts_code(symbol)
        api = self._ts()
        start_str = start_date.replace("-", "")
        end_str = end_date.replace("-", "")

        try:
            df = api.daily(ts_code=ts_code, start_date=start_str, end_date=end_str)
        except Exception as exc:
            raise NotImplementedError(
                f"cn_tushare daily query failed: {type(exc).__name__}: {exc}"
            ) from exc

        if df is None or df.empty:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

        norm = self._normalize_hist_df(df)
        return self._format_hist_csv(norm, symbol, start_date, end_date)

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        if indicator not in self.INDICATOR_DESCRIPTIONS:
            raise ValueError(
                f"Indicator {indicator} is not supported. "
                f"Please choose from: {list(self.INDICATOR_DESCRIPTIONS.keys())}"
            )

        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=max(look_back_days, 260))
        ts_code = self._normalize_ts_code(symbol)
        api = self._ts()

        try:
            df = api.daily(
                ts_code=ts_code,
                start_date=start_dt.strftime("%Y%m%d"),
                end_date=curr_dt.strftime("%Y%m%d"),
            )
        except Exception as exc:
            raise NotImplementedError(
                f"cn_tushare daily query failed for indicators: {type(exc).__name__}: {exc}"
            ) from exc

        if df is None or df.empty:
            return f"No data found for {symbol} for indicator {indicator}"

        norm = self._normalize_hist_df(df)
        ind_df = norm.rename(
            columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )[["date", "open", "high", "low", "close", "volume"]].copy()

        ss = wrap(ind_df)
        indicator_series = ss[indicator]

        values_by_date = {}
        for idx, dt_val in enumerate(ind_df["date"]):
            date_str = pd.to_datetime(dt_val).strftime("%Y-%m-%d")
            val = indicator_series.iloc[idx]
            values_by_date[date_str] = "N/A" if pd.isna(val) else str(val)

        begin = curr_dt - timedelta(days=look_back_days)
        lines = []
        d = curr_dt
        while d >= begin:
            key = d.strftime("%Y-%m-%d")
            value = values_by_date.get(key)
            if value is None or value == "N/A":
                value = cn_no_data_reason(key)
            lines.append(f"{key}: {value}")
            d -= timedelta(days=1)

        result = (
            f"## {indicator} 指标值（{begin.strftime('%Y-%m-%d')} 至 {curr_date}）：\n\n"
            + "\n".join(lines)
            + "\n\n"
            + self.INDICATOR_DESCRIPTIONS[indicator]
        )
        return result

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        ts_code = self._normalize_ts_code(ticker)
        api = self._ts()
        errors = []

        parts = [f"## Fundamentals for {ticker}"]

        # Financial indicators
        try:
            df = api.fina_indicator(ts_code=ts_code)
            if df is not None and not df.empty:
                df = df.sort_values("end_date", ascending=False).head(4)
                # Convert YYYYMMDD dates
                if "end_date" in df.columns:
                    df["end_date"] = pd.to_datetime(
                        df["end_date"].astype(str), format="%Y%m%d", errors="coerce"
                    ).dt.strftime("%Y-%m-%d")
                parts.append("### Financial Indicators (latest periods)")
                parts.append(self._shrink_table(df, max_rows=8, max_cols=14).to_markdown(index=False))
        except Exception as exc:
            errors.append(f"fina_indicator: {type(exc).__name__}")

        # Company info via stock_company
        try:
            df = api.stock_company(ts_code=ts_code, fields="ts_code,exchange,chairman,manager,secretary,reg_capital,setup_date,employees,main_business")
            if df is not None and not df.empty:
                parts.append("### Company Profile")
                parts.append(df.head(1).to_markdown(index=False))
        except Exception as exc:
            errors.append(f"stock_company: {type(exc).__name__}")

        if len(parts) > 1:
            return "\n\n".join(parts)

        raise NotImplementedError(
            f"cn_tushare is temporarily unavailable for fundamentals: {'; '.join(errors)}"
        )

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        ts_code = self._normalize_ts_code(ticker)
        api = self._ts()
        try:
            df = api.balancesheet(ts_code=ts_code)
            if df is None or df.empty:
                return f"No balance sheet data found for {ticker}"
            df = df.sort_values("end_date", ascending=False).head(4)
            if "end_date" in df.columns:
                df["end_date"] = pd.to_datetime(
                    df["end_date"].astype(str), format="%Y%m%d", errors="coerce"
                ).dt.strftime("%Y-%m-%d")
            return (
                f"## Balance Sheet ({ticker})\n\n"
                + self._shrink_table(df, max_rows=12, max_cols=18).to_markdown(index=False)
            )
        except Exception as exc:
            raise NotImplementedError(
                f"cn_tushare balancesheet query failed: {type(exc).__name__}: {exc}"
            ) from exc

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        ts_code = self._normalize_ts_code(ticker)
        api = self._ts()
        try:
            df = api.cashflow(ts_code=ts_code)
            if df is None or df.empty:
                return f"No cashflow data found for {ticker}"
            df = df.sort_values("end_date", ascending=False).head(4)
            if "end_date" in df.columns:
                df["end_date"] = pd.to_datetime(
                    df["end_date"].astype(str), format="%Y%m%d", errors="coerce"
                ).dt.strftime("%Y-%m-%d")
            return (
                f"## Cashflow ({ticker})\n\n"
                + self._shrink_table(df, max_rows=12, max_cols=18).to_markdown(index=False)
            )
        except Exception as exc:
            raise NotImplementedError(
                f"cn_tushare cashflow query failed: {type(exc).__name__}: {exc}"
            ) from exc

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        ts_code = self._normalize_ts_code(ticker)
        api = self._ts()
        try:
            df = api.income(ts_code=ts_code)
            if df is None or df.empty:
                return f"No income statement data found for {ticker}"
            df = df.sort_values("end_date", ascending=False).head(4)
            if "end_date" in df.columns:
                df["end_date"] = pd.to_datetime(
                    df["end_date"].astype(str), format="%Y%m%d", errors="coerce"
                ).dt.strftime("%Y-%m-%d")
            return (
                f"## Income Statement ({ticker})\n\n"
                + self._shrink_table(df, max_rows=12, max_cols=18).to_markdown(index=False)
            )
        except Exception as exc:
            raise NotImplementedError(
                f"cn_tushare income query failed: {type(exc).__name__}: {exc}"
            ) from exc

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        ts_code = self._normalize_ts_code(ticker)
        api = self._ts()
        errors = []

        # Try news_cnt (requires higher Tushare points)
        try:
            df = api.news_cnt(ts_code=ts_code, start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""))
            if df is not None and not df.empty:
                # Fetch actual news content via news_detail if available
                rows = []
                for _, row in df.head(20).iterrows():
                    title = str(row.get("title", "No title"))
                    content = str(row.get("content", ""))
                    date_val = str(row.get("date", ""))
                    src = str(row.get("src", ""))
                    rows.append(f"### {title} (source: {src}, date: {date_val})")
                    if content and content != "nan":
                        rows.append(content[:400])
                    rows.append("")
                if rows:
                    return (
                        f"## {ticker} 新闻（{start_date} 至 {end_date}）：\n\n"
                        + "\n".join(rows)
                    )
        except Exception as exc:
            errors.append(f"news_cnt: {type(exc).__name__}")

        # Fallback: use major_news
        try:
            df = api.major_news(start_date=start_date.replace("-", ""))
            if df is not None and not df.empty:
                # Filter by ticker if company column exists
                if "company" in df.columns:
                    df = df[df["company"].str.contains(ticker, na=False)]
                if not df.empty:
                    rows = []
                    for _, row in df.head(20).iterrows():
                        title = str(row.get("title", "No title"))
                        date_val = str(row.get("date", ""))
                        content = str(row.get("content", ""))
                        rows.append(f"### {title} (date: {date_val})")
                        if content and content != "nan":
                            rows.append(content[:400])
                        rows.append("")
                    if rows:
                        return (
                            f"## {ticker} 财经新闻（{start_date} 至 {end_date}）：\n\n"
                            + "\n".join(rows)
                        )
        except Exception as exc:
            errors.append(f"major_news: {type(exc).__name__}")

        raise NotImplementedError(
            f"cn_tushare is temporarily unavailable for news: {'; '.join(errors)}"
        )

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        raise NotImplementedError("cn_tushare does not provide global news yet.")

    def get_insider_transactions(self, symbol: str) -> str:
        ts_code = self._normalize_ts_code(symbol)
        api = self._ts()
        errors = []

        # Try holder trading data
        try:
            df = api.holder_trade(ts_code=ts_code)
            if df is not None and not df.empty:
                df = df.sort_values("end_date", ascending=False).head(20) if "end_date" in df.columns else df.head(20)
                return (
                    f"## Insider Transactions for {symbol}\n\n"
                    + df.to_markdown(index=False)
                )
        except Exception as exc:
            errors.append(f"holder_trade: {type(exc).__name__}")

        # Fallback: holder_number (top shareholders)
        try:
            df = api.top10_holders(ts_code=ts_code)
            if df is not None and not df.empty:
                df = df.sort_values("end_date", ascending=False).head(20) if "end_date" in df.columns else df.head(20)
                return (
                    f"## Insider Transactions for {symbol}\n\n"
                    f"未获取到股东交易明细，降级返回前十大股东信息：\n\n"
                    + df.to_markdown(index=False)
                )
        except Exception as exc:
            errors.append(f"top10_holders: {type(exc).__name__}")

        raise NotImplementedError(
            f"cn_tushare is temporarily unavailable for insider transactions: {'; '.join(errors)}"
        )

    def get_realtime_quotes(self, symbols: list[str]) -> str:
        """Tushare is not a real-time data provider. Try today's daily data as fallback."""
        import json

        result: dict[str, dict] = {}
        ts_api = None

        try:
            ts_api = self._ts()
        except NotImplementedError:
            return json.dumps({})

        for s in symbols:
            if not s or not s.strip():
                continue
            try:
                ts_code = self._normalize_ts_code(s)
            except NotImplementedError:
                continue

            today = datetime.now().strftime("%Y%m%d")
            try:
                df = ts_api.daily(ts_code=ts_code, start_date=today, end_date=today)
            except Exception:
                continue

            if df is None or df.empty:
                continue

            row = df.iloc[0]
            price = self._safe_float(row.get("close"))
            prev_close = self._safe_float(row.get("pre_close"))
            change = round(price - prev_close, 4) if price is not None and prev_close else None
            change_pct = round(change / prev_close * 100, 4) if change is not None and prev_close else None
            result[s.strip().upper()] = {
                "price": price,
                "open": self._safe_float(row.get("open")),
                "high": self._safe_float(row.get("high")),
                "low": self._safe_float(row.get("low")),
                "previous_close": prev_close,
                "change": change,
                "change_pct": change_pct,
                "volume": self._safe_float(row.get("vol")),
                "source": "tushare",
            }

        return json.dumps(result, ensure_ascii=False) if result else json.dumps({})

    @staticmethod
    def _safe_float(val) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)
            return f if not pd.isna(f) else None
        except (ValueError, TypeError):
            return None
