"""
板块主线分析管理器

协调数据获取、评分、存储的完整流程。
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .sector_source import (
    fetch_sector_list,
    fetch_sector_klines,
    fetch_sector_constituents,
    fetch_market_index_klines,
)
from .sector_scorer import score_sector
from .sector_storage import SectorStorage

# 预筛选数量：仅对涨幅前N的板块拉取K线（避免对全部~500个板块逐只请求）
_PREFILTER_N = 60


class SectorManager:
    """板块主线分析管理器"""

    def __init__(self):
        self._storage = SectorStorage()
        self._lock = threading.Lock()

    def run_analysis(self, top_n: int = 30) -> dict:
        now = datetime.now()
        scan_date = now.strftime("%Y-%m-%d")
        scan_time = now.strftime("%Y-%m-%d %H:%M:%S")

        # 1. 获取大盘K线（RS基准）+ 板块列表
        market_klines = fetch_market_index_klines(count=30)
        industry_sectors = fetch_sector_list("industry")
        concept_sectors = fetch_sector_list("concept")

        # 判断是否为本地TDX降级模式（行业和概念名称相同则为降级模式）
        is_local_fallback = (industry_sectors and concept_sectors
                             and industry_sectors[0].get("name", "").startswith("板块")
                             and concept_sectors[0].get("name", "").startswith("板块"))

        if is_local_fallback:
            # 本地降级：行业和概念来自同一目录，去重合并
            all_sectors = industry_sectors  # 已包含全部
            industry_set = set()
            concept_set = set()
        else:
            # EM模式：行业和概念是独立数据
            industry_set = {s["name"] for s in industry_sectors}
            concept_set = {s["name"] for s in concept_sectors}
            all_sectors = industry_sectors + concept_sectors

        # 2. 第一轮评分：仅用EM数据（涨跌幅/涨跌家数/成交额），不请求K线
        all_scored = self._score_em_only(all_sectors, market_klines)

        # 3. 按第一轮分数排序，取前 _PREFILTER_N 个拉取K线
        all_scored.sort(key=lambda x: x["total_score"], reverse=True)
        candidates = all_scored[:_PREFILTER_N]

        # 4. 并行拉取候选板块K线
        kline_map = self._fetch_klines_batch(candidates)

        # 5. 第二轮评分：用K线数据重新计算RS和动量维度
        industry_results = []
        concept_results = []

        for scored in all_scored:
            name = scored["name"]
            klines = kline_map.get(name)
            if klines is not None:
                sector_data = scored.get("_sector_data", {})
                scored = score_sector(sector_data, klines, market_klines)

            if is_local_fallback:
                # 本地降级：全部归入industry
                industry_results.append(scored)
            elif name in industry_set:
                industry_results.append(scored)
            elif name in concept_set:
                concept_results.append(scored)

        # 6. 分类排序
        industry_results.sort(key=lambda x: x["total_score"], reverse=True)
        concept_results.sort(key=lambda x: x["total_score"], reverse=True)
        industry_results = industry_results[:top_n]
        concept_results = concept_results[:top_n]

        all_results = industry_results + concept_results
        mainline = sorted(
            [r for r in all_results if r["category"] == "mainline"],
            key=lambda x: x["total_score"], reverse=True
        )
        potential = sorted(
            [r for r in all_results if r["category"] == "potential"],
            key=lambda x: x["total_score"], reverse=True
        )

        fading = self._detect_fading(mainline)

        result = {
            "scan_date": scan_date,
            "scan_time": scan_time,
            "industry": industry_results,
            "concept": concept_results,
            "mainline": mainline[:20],
            "potential": potential[:20],
            "fading": fading,
            "market_klines_count": len(market_klines),
            "data_source": "TDX+EM",
        }

        self._storage.save_snapshot(result)
        self._storage.add_history_entry({
            "date": scan_date,
            "mainline": [s["name"] for s in mainline[:10]],
            "potential": [s["name"] for s in potential[:10]],
            "fading": [s["name"] for s in fading],
        })

        return result

    def _score_em_only(self, sectors: list[dict], market_klines: list[dict]) -> list[dict]:
        """第一轮评分：仅用EM数据（不需要K线），用于预筛选"""
        results = []
        for sector in sectors:
            scored = score_sector(sector, [], market_klines)
            scored["_sector_data"] = sector  # 保留原始数据供第二轮使用
            results.append(scored)
        return results

    def _fetch_klines_batch(self, candidates: list[dict]) -> dict:
        """
        并行拉取候选板块K线。

        策略: 有index_code的走TDX TCP（快），无index_code的走EM HTTP（慢但并行）。
        TDX TCP请求约200ms，EM HTTP约1-3s，并行8个worker可显著缩短总耗时。
        """
        kline_map = {}  # name -> klines

        def _fetch_one(sector):
            idx = sector.get("index_code", "")
            code = sector.get("code", "")
            name = sector.get("name", "")
            klines = fetch_sector_klines(idx, code, count=30)
            return name, klines

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_fetch_one, s): s for s in candidates}
            for future in as_completed(futures):
                try:
                    name, klines = future.result()
                    kline_map[name] = klines
                except Exception:
                    s = futures[future]
                    kline_map[s.get("name", "")] = []

        return kline_map

    def _detect_fading(self, current_mainline: list[dict]) -> list[dict]:
        history = self._storage.load_history()
        if not history:
            return []
        prev = history[-1]
        prev_names = set(prev.get("mainline", []))
        curr_names = {s["name"] for s in current_mainline}
        return [{"name": n, "category": "fading"} for n in prev_names - curr_names]

    def get_latest(self) -> dict | None:
        return self._storage.load_snapshot()

    def get_history(self) -> list[dict]:
        return self._storage.load_history()

    def get_sector_detail(self, sector_name: str) -> dict | None:
        snapshot = self._storage.load_snapshot()
        if not snapshot:
            return None
        for group in ("industry", "concept"):
            for s in snapshot.get(group, []):
                if s["name"] == sector_name:
                    return s
        return None

    def get_sector_stocks(self, sector_code: str) -> list[tuple[str, str]]:
        return fetch_sector_constituents(sector_code)


# ── 全局单例 ──────────────────────────────────────────────

_manager = None


def get_sector_manager() -> SectorManager:
    global _manager
    if _manager is None:
        _manager = SectorManager()
    return _manager
