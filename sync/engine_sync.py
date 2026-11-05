# -*- coding: utf-8 -*-
"""engine_sync.py —— 数据同步与复盘 插件的核心逻辑。

迁移自主程序：
  · update_all.py   -> sync_all()：全量刷新曲库/热词/流量/结算，缓存到账号目录
  · daily_review.py -> build_review()：由缓存数据生成每日复盘文本
  · retro_analysis.py -> build_retro()：由缓存数据生成阶段回顾报告
  · gen_monitor.py  -> monitor()：监控（只保留不依赖缺失模块的安全部分）

硬规则：
  · 不 import 主程序；只用 ctx.api / ctx.account_save_json / ctx.account_load_json
  · 不自己拼账号路径
  · 全程防御式：单个接口失败只记错误、填默认值，绝不抛异常到宿主

所有对外缓存都按"账号隔离"存到 ctx.account_* 下（与 revenue 插件用 store 不同，
sync 的数据天然是每账号一份，brief 明确要求用 account_save_json）。
"""
from __future__ import annotations

import datetime
import json

# ------------------------------------------------------------- 缓存文件名
CACHE_WORKS = "sync_works_cache.json"
CACHE_HOT = "sync_hot_content.json"
CACHE_TRAFFIC_REV = "sync_traffic_revenue.json"
CACHE_TRAFFIC_COMP = "sync_traffic_composition.json"
CACHE_SETTLE = "sync_settlement.json"
CACHE_HOME = "sync_home.json"
CACHE_REVIEW = "sync_review_cache.json"
CACHE_RETRO = "sync_retro_cache.json"
CACHE_LAST = "sync_last.json"

# 可抓取的小节（每一项缓存一个文件）
SECTION_FILES = {
    "works": CACHE_WORKS,
    "hot": CACHE_HOT,
    "traffic_revenue": CACHE_TRAFFIC_REV,
    "traffic_composition": CACHE_TRAFFIC_COMP,
    "settlement": CACHE_SETTLE,
    "home": CACHE_HOME,
}


# ------------------------------------------------------------- 小工具
def ts_now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.date.today().isoformat()


def _load(ctx, name, default=None):
    try:
        return ctx.account_load_json(name, default)
    except Exception:
        return default


def _save(ctx, name, obj):
    try:
        ctx.account_save_json(name, obj)
        return True
    except Exception:
        return False


def _i(x):
    try:
        return int(float(x or 0))
    except Exception:
        return 0


# ------------------------------------------------------------- 全量同步
def sync_all(ctx):
    """全量刷新当前账号的曲库/热词/流量/结算，逐节防御、各自缓存。

    返回结构化 dict（含 ok 总开关 / updated 已成功小节数 / synced_at /
    sections 各节明细 / errors 失败列表）。任何小节失败都不影响其它小节，
    也绝不把异常冒泡出去。
    """
    api = ctx.api
    port = getattr(ctx, "port", None)
    log = getattr(ctx, "log", None)
    uid = getattr(ctx, "uid", "") or ""
    ts = ts_now()

    sections = {}
    errors = []
    updated = 0

    # 1) 曲库（作品全量）
    try:
        works = list(api.iter_works(size=1000, port=port, verbose=False))
        _save(ctx, CACHE_WORKS, {"saved_at": ts, "uid": uid,
                                  "total": len(works), "works": works})
        sections["works"] = len(works)
        updated += 1
    except Exception as e:  # noqa: BLE001
        errors.append("works:%s" % e)
        if log:
            try:
                log("sync 曲库失败: %s" % e)
            except Exception:
                pass

    # 2) 热词 / 榜单
    try:
        hc = api.hot_content(port=port)
        _save(ctx, CACHE_HOT, {"saved_at": ts, "uid": uid, "data": hc})
        sections["hot"] = 1
        updated += 1
    except Exception as e:  # noqa: BLE001
        errors.append("hot_content:%s" % e)
        if log:
            try:
                log("sync 热词失败: %s" % e)
            except Exception:
                pass

    # 3) 流量收益（账号汇总：汽水收藏/评论、抖音涨粉/视频播放）
    try:
        tr = api.traffic_revenue(port=port)
        _save(ctx, CACHE_TRAFFIC_REV, {"saved_at": ts, "uid": uid, "data": tr})
        sections["traffic_revenue"] = 1
        updated += 1
    except Exception as e:  # noqa: BLE001
        errors.append("traffic_revenue:%s" % e)

    # 4) 流量来源饼图（冷启动 / 打歌视频曝光）
    try:
        comp = api.traffic_composition(port=port)
        _save(ctx, CACHE_TRAFFIC_COMP, {"saved_at": ts, "uid": uid, "data": comp})
        sections["traffic_composition"] = 1
        updated += 1
    except Exception as e:  # noqa: BLE001
        errors.append("traffic_composition:%s" % e)

    # 5) 结算概览（金额单位已是元）
    try:
        so = api.settlement_overview(port=port)
        _save(ctx, CACHE_SETTLE, {"saved_at": ts, "uid": uid, "data": so})
        sections["settlement"] = 1
        updated += 1
    except Exception as e:  # noqa: BLE001
        errors.append("settlement:%s" % e)

    # 6) 首页概览（现金收入/汽水热度/抖音视频播放等轻量汇总）
    try:
        hp = api.home_page_info(port=port)
        _save(ctx, CACHE_HOME, {"saved_at": ts, "uid": uid, "data": hp})
        sections["home"] = 1
        updated += 1
    except Exception as e:  # noqa: BLE001
        errors.append("home_page_info:%s" % e)

    ok = updated > 0
    report = {
        "ok": ok,
        "updated": updated,
        "total_sections": len(SECTION_FILES),
        "synced_at": ts,
        "uid": uid,
        "sections": sections,
        "errors": errors,
    }
    _save(ctx, CACHE_LAST, {"synced_at": ts, "updated": updated, "ok": ok,
                            "sections": sections, "errors": errors})
    return report


# ------------------------------------------------------------- 读缓存聚合
def _collect(ctx):
    """把各缓存读出来，拼成一个供复盘/回顾使用的 dict。读不到就返回空。"""
    out = {}
    w = _load(ctx, CACHE_WORKS, {}) or {}
    out["works_total"] = _i(w.get("total"))
    out["works"] = w.get("works") or []
    out["works_saved_at"] = w.get("saved_at", "")

    tr = (_load(ctx, CACHE_TRAFFIC_REV, {}) or {}).get("data") or {}
    out["luna_collections"] = tr.get("luna_collections")
    out["luna_comments"] = tr.get("luna_comments")
    out["douyin_new_followers"] = tr.get("douyin_new_followers")
    out["douyin_video_plays"] = tr.get("douyin_video_plays")
    out["douyin_video_uses"] = tr.get("douyin_video_uses")

    comp = (_load(ctx, CACHE_TRAFFIC_COMP, {}) or {}).get("data") or {}
    out["composition"] = comp

    so = (_load(ctx, CACHE_SETTLE, {}) or {}).get("data") or {}
    out["cumulative_income"] = so.get("cumulative_income")
    out["withdrawable"] = so.get("withdrawable")
    out["withdrawn"] = so.get("withdrawn")
    out["last_period_income"] = so.get("last_period_income")
    out["last_period_time"] = so.get("last_period_time")

    hp = (_load(ctx, CACHE_HOME, {}) or {}).get("data") or {}
    out["home"] = hp

    hc = (_load(ctx, CACHE_HOT, {}) or {}).get("data") or {}
    out["hot_songs"] = hc.get("hot_songs") or []
    out["hot_artists"] = hc.get("hot_artists") or []
    out["hot_videos"] = hc.get("hot_videos") or []
    return out


def _fmt_money(v):
    try:
        return "%.2f 元" % float(v)
    except Exception:
        return "—" if v in (None, "") else str(v)


def _fmt_int(v):
    try:
        return "{:,}".format(int(v))
    except Exception:
        return "—" if v in (None, "") else str(v)


def _works_model_dist(works):
    """作品按创作模型分布（来自曲库缓存，没有就空）。"""
    d = {}
    for w in works or []:
        if not isinstance(w, dict):
            continue
        m = w.get("StudioCreationModel") or w.get("studioCreationModel") or "未知"
        d[m] = d.get(m, 0) + 1
    return d


# ------------------------------------------------------------- 每日复盘
def build_review(ctx, ts=None):
    """由缓存数据生成每日复盘文本（纯本地，不联网）。无数据返回提示。"""
    d = _collect(ctx)
    ts = ts or ts_now()
    date = ts.split(" ")[0]
    if not d.get("works") and d.get("luna_collections") is None and \
            d.get("cumulative_income") is None and not d.get("hot_songs"):
        return ("# 数据复盘 · %s\n\n尚未同步到任何数据。\n"
                "请先点【立即同步】拉取曲库/流量/结算，再生成复盘。" % date)

    L = []
    A = L.append
    A("# 数据复盘 · %s" % date)
    A("")
    A("> 数据来自最近一次同步的本地缓存（曲库 / 流量 / 结算 / 热词）。")
    A("")

    # 曲库
    A("## 一、曲库")
    A("")
    A("- 作品总数：**%s** 首" % _fmt_int(d.get("works_total")))
    md = _works_model_dist(d.get("works"))
    if md:
        A("- 创作模型分布：%s" % "，".join("%s %d 首" % (k, v) for k, v in
                                        sorted(md.items(), key=lambda kv: -kv[1])))
    A("")

    # 流量与互动
    A("## 二、流量与互动（汽水 / 抖音）")
    A("")
    A("- 汽水收藏数：**%s**" % _fmt_int(d.get("luna_collections")))
    A("- 汽水评论数：**%s**" % _fmt_int(d.get("luna_comments")))
    A("- 抖音涨粉：**%s**" % _fmt_int(d.get("douyin_new_followers")))
    A("- 抖音视频播放：**%s**" % _fmt_int(d.get("douyin_video_plays")))
    A("- 抖音视频使用：**%s**" % _fmt_int(d.get("douyin_video_uses")))
    comp = d.get("composition") or {}
    if comp:
        items = comp.get("items") or comp.get("raw", {}).get("items") or []
        if items:
            A("- 流量来源（饼图）：%s" % "，".join(
                _fmt_comp_item(it) for it in items[:4]))
    A("")

    # 结算
    A("## 三、结算收益")
    A("")
    A("- 累计收益：**%s**" % _fmt_money(d.get("cumulative_income")))
    A("- 可提现：**%s**" % _fmt_money(d.get("withdrawable")))
    A("- 已提现：**%s**" % _fmt_money(d.get("withdrawn")))
    lp = d.get("last_period_income")
    if lp is not None:
        A("- 上期收益：**%s**" % _fmt_money(lp))
        if d.get("last_period_time"):
            A("- 上期时间：%s" % d.get("last_period_time"))
    A("")

    # 热词 / 榜单
    A("## 四、站内热词 / 榜单")
    A("")
    hs = d.get("hot_songs") or []
    if hs:
        A("热门歌曲 Top：")
        for x in hs[:10]:
            nm = _pick(x, ["songName", "title", "name"]) or ""
            if nm:
                A("- %s" % nm)
    else:
        A("- （热词榜暂无数据）")
    A("")

    A("---")
    A("生成时间：%s" % ts)
    return "\n".join(L)


# ------------------------------------------------------------- 阶段回顾
def build_retro(ctx, ts=None):
    """由缓存数据生成阶段回顾报告（纯本地，不联网）。无数据返回提示。"""
    d = _collect(ctx)
    ts = ts or ts_now()
    if not d.get("works") and d.get("cumulative_income") is None and \
            d.get("luna_collections") is None:
        return ("# 阶段回顾\n\n尚未同步到任何数据。\n"
                "请先点【立即同步】拉取曲库/流量/结算后再生成回顾。")

    L = []
    A = L.append
    A("# 阶段回顾报告")
    A("")
    A("> 数据口径：单曲级数据平台只给到「逐歌 × 按月」，本插件按账号汇总口径回顾。")
    A("> 全部来自最近一次同步的本地缓存。")
    A("")

    A("## 一、曲库规模")
    A("")
    A("- 在库作品：**%s** 首" % _fmt_int(d.get("works_total")))
    md = _works_model_dist(d.get("works"))
    if md:
        A("- 创作模型分布（可判断产能结构）：")
        for k, v in sorted(md.items(), key=lambda kv: -kv[1]):
            A("  - %s：%d 首（%.0f%%）" % (k, v, v * 100.0 / max(1, d.get("works_total"))))
    A("")

    A("## 二、流量资产（累计口径）")
    A("")
    A("- 汽水累计收藏：**%s**" % _fmt_int(d.get("luna_collections")))
    A("- 汽水累计评论：**%s**" % _fmt_int(d.get("luna_comments")))
    A("- 抖音累计视频播放：**%s**" % _fmt_int(d.get("douyin_video_plays")))
    A("- 抖音累计视频使用（被用作素材）：**%s**" % _fmt_int(d.get("douyin_video_uses")))
    A("")
    A("> 收藏/评论代表「用户主动喜欢」，视频使用代表「被拿去二次创作」——"
      "后者是抖音侧传播的核心信号。")
    A("")

    A("## 三、收益与结算")
    A("")
    A("- 累计收益：**%s**" % _fmt_money(d.get("cumulative_income")))
    A("- 可提现余额：**%s**" % _fmt_money(d.get("withdrawable")))
    A("- 已提现：**%s**" % _fmt_money(d.get("withdrawn")))
    A("")
    A("> 若累计收益为 0/空，多半是「当前登录的不是产生收益的创作者账号」或「该账号还没有结算单」。")
    A("")

    A("## 四、结论与下一步")
    A("")
    A("1. 流量集中在收藏/评论还是视频使用，决定下一步是「做更多可被切片的内容」"
      "还是「提升单曲质量」。")
    A("2. 按创作模型分布优化产能：占比过高的模型若播放平平，应换方向试产。")
    A("3. 收益为空时先确认登录的是正确的创作者账号，再判断是否需要补推抖音。")
    A("")
    A("---")
    A("生成时间：%s" % ts)
    return "\n".join(L)


# ------------------------------------------------------------- 监控
def monitor(ctx):
    """gen_monitor 的安全移植：只保留不依赖缺失模块（CDP / studio）的部分。

    主程序 gen_monitor 的核心价值是「今天到底出歌了没」。在插件里我们无法可靠
    读取创作台（要 CDP + studio_automation，那两个模块不能 import），所以这里
    退化成一个**基于本地同步事实**的 verdict：今天有没有成功同步过。
    返回 {date, synced_today, verdict, note}。绝不抛异常。
    """
    last = _load(ctx, CACHE_LAST, {}) or {}
    today = today_str()
    sa = last.get("synced_at") or ""
    synced_today = sa.startswith(today)
    if synced_today:
        verdict = "normal"
        note = "今日已同步（%s），数据新鲜。" % sa
    else:
        verdict = "unknown"
        note = ("今日尚未同步（上次 %s）。点【立即同步】刷新曲库/流量/结算。"
                % (sa or "从未"))
    return {"date": today, "synced_today": synced_today,
            "verdict": verdict, "note": note}


# ------------------------------------------------------------- 缓存读回（给 UI）
def load_review(ctx):
    c = _load(ctx, CACHE_REVIEW, {}) or {}
    return c.get("text") or build_review(ctx)


def load_retro(ctx):
    c = _load(ctx, CACHE_RETRO, {}) or {}
    return c.get("text") or build_retro(ctx)


def load_last(ctx):
    return _load(ctx, CACHE_LAST, {}) or {}


def save_review_retro(ctx, review, retro):
    ts = ts_now()
    _save(ctx, CACHE_REVIEW, {"saved_at": ts, "text": review})
    _save(ctx, CACHE_RETRO, {"saved_at": ts, "text": retro})


# ------------------------------------------------------------- 辅助
def _pick(d, keys):
    for k in keys:
        v = d.get(k)
        if v:
            return str(v)
    return ""


def _fmt_comp_item(it):
    try:
        cat = it.get("category")
        val = it.get("value")
        return "类别%d %s" % (cat, _fmt_int(val))
    except Exception:
        return str(it)
