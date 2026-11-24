"""生物课模拟器的规则和存档。"""

import json
import logging
import random
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path

GAME_TZ = timezone(timedelta(hours=8))
MAX_SCORE = 100

QUOTES = (
    "老师说这一页不考，我的海马体当场释放了内存。",
    "熬夜背生物，最先适应环境的是我的黑眼圈。",
    "课本里的反射弧很短，老师点到我时却很长。",
    "今天的我和叶绿体达成共识：都想多晒一会儿太阳。",
    "一节课有四十五分钟，下课前最后一分钟有四十五节课那么长。",
    "遗传题不是家谱调查，但我还是替题里的豌豆操碎了心。",
    "写完作业那一刻，感觉整个教室都在进行光合作用。",
    "细胞呼吸一直在进行，我读到最后一道题时忘了呼吸。",
    "草稿纸上写满了思路，答题卡上留下了最勇敢的那个。",
    "同桌翻页的声音，是考场上最没有参考价值的提示。",
    "显微镜能放大细胞，老师一句「这里会考」能放大整页。",
    "我试图在睡梦中复习，结果只记得梦里也没带课本。",
)

HELP = """生物课模拟器
/生物状态｜查看成绩、精力、作业和试卷
/上课｜开始一节生物课
/答题｜尝试回答，答错后用同一指令重答
/睡觉｜本节课用来恢复精力
/默写｜每日默写，失败后用同一指令重默
/签订协议｜查看永久免默写协议
/确认签约｜确认签订协议
/偷试卷｜签约后可尝试，偷到的试卷计入库存
/写作业｜完成一份作业并加分
/抄作业｜抄同桌的作业，有概率触发「同」
/每日金句｜每天抽一句"""


@dataclass(frozen=True)
class Rules:
    initial_score: int
    daily_score_decay: int
    max_energy: int
    daily_classes: int
    class_minutes: int
    answer_cost: int
    answer_percent: float
    answer_points: int
    nap_energy: int
    dictation_cost: int
    dictation_percent: float
    dictation_points: int
    dictation_homework: int
    bonus_days: int
    bonus_points: int
    steal_cost: int
    steal_percent: float
    daily_homework: int
    homework_cost: int
    homework_points: int
    copy_cost: int
    copy_penalty_percent: float
    copy_penalty_days: int
    quotes: tuple[str, ...]

    @classmethod
    def load(cls, config: Mapping) -> "Rules":
        schema = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
        values = {}
        positive = {"max_energy", "class_minutes", "bonus_days", "copy_penalty_days"}
        for name, spec in schema.items():
            value = config.get(name, spec["default"])
            if name == "quotes":
                values[name] = cls.parse_quotes(value)
                continue
            if spec["type"] == "float":
                valid = type(value) in (int, float) and 0 <= value <= 100
            else:
                minimum = 1 if name in positive else 0
                maximum = MAX_SCORE if name == "initial_score" else 1_000_000
                valid = type(value) is int and minimum <= value <= maximum
            if not valid:
                logging.getLogger(__name__).warning("配置项 %s 无效，使用默认值", name)
                value = spec["default"]
            values[name] = value
        return cls(**values)

    @staticmethod
    def parse_quotes(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            return QUOTES
        return tuple(quote.strip() for quote in value if isinstance(quote, str) and quote.strip()) or QUOTES


@dataclass
class Player:
    score: int
    energy: int
    day: int = 0
    classes_today: int = 0
    class_ends_at: float = 0
    class_done: bool = False
    answer_pending: bool = False
    dictation_done: bool = False
    dictation_pending: bool = False
    signed: bool = False
    bonus_until: int = 0
    bonus_points: int = 1
    copy_blocked_until: int = 0
    daily_homework: int = 0
    dictation_homework: int = 0
    papers: int = 0
    quote: str = ""

    def settle_day(self, today: int, rules: Rules) -> None:
        self.energy = min(self.energy, rules.max_energy)
        if today <= self.day:
            return
        if self.day:
            elapsed_days = today - self.day
            bonus_days = max(0, min(today + 1, self.bonus_until) - (self.day + 1))
            # 红利到期前后分别结算，保留每天的成绩上下限。
            self.add_score(bonus_days * (self.bonus_points - rules.daily_score_decay))
            self.add_score(-(elapsed_days - bonus_days) * rules.daily_score_decay)
        self.day = today
        self.energy = rules.max_energy
        self.classes_today = 0
        self.dictation_done = False
        self.quote = ""
        if not self.daily_homework and not self.dictation_homework:
            self.daily_homework = rules.daily_homework

    def add_score(self, points: int) -> int:
        actual = max(-self.score, min(points, MAX_SCORE - self.score))
        self.score += actual
        return actual

    def spend(self, cost: int) -> None:
        if self.energy < cost:
            raise MoveUnavailable(f"精力不足，需要 {cost} 点，当前 {self.energy} 点。")
        self.energy -= cost


class MoveUnavailable(Exception):
    """玩家当前无法执行这一步。"""


def status(p: Player, rules: Rules, now: float) -> str:
    if p.answer_pending:
        lesson = "有一道课上问题待重答"
    elif now < p.class_ends_at:
        minutes = ceil((p.class_ends_at - now) / 60)
        choice = "已完成课堂活动" if p.class_done else "可答题或睡觉"
        lesson = f"上课中，剩 {minutes} 分钟，{choice}"
    else:
        lesson = "未在上课"
    dictation = "今日待默写"
    if p.dictation_done:
        dictation = "今日已过关"
    if p.dictation_pending:
        dictation = "待重默"
    if p.signed:
        dictation = "已签约免默写"
    lines = [
        f"生物成绩 {p.score}/{MAX_SCORE} · 精力 {p.energy}/{rules.max_energy}",
        f"今日上课 {p.classes_today}/{rules.daily_classes} · {lesson}",
        f"默写：{dictation}",
        f"作业：每日 {p.daily_homework} 份，默写奖励 {p.dictation_homework} 份",
        f"试卷：{p.papers} 张",
    ]
    if p.bonus_until > p.day:
        lines.append(f"「红利」剩 {p.bonus_until - p.day} 天（含今日）")
    if p.copy_blocked_until > p.day:
        lines.append(f"「同」剩 {p.copy_blocked_until - p.day} 天（含今日），只能自己写作业")
    return "\n".join(lines)


def start_class(p: Player, rules: Rules, now: float) -> str:
    if p.answer_pending:
        return "还有一道课上问题没答对，请先用「答题」重答。"
    if now < p.class_ends_at:
        return status(p, rules, now)
    if rules.daily_classes == 0:
        return "生物课目前暂停开课。"
    if p.classes_today >= rules.daily_classes:
        return "今天的生物课已经上满了，明天再来。"
    p.classes_today += 1
    p.class_ends_at = now + rules.class_minutes * 60
    p.class_done = False
    return f"第 {p.classes_today} 节生物课开始了，{rules.class_minutes} 分钟后下课。请选择「答题」或「睡觉」。"


def answer(p: Player, rules: Rules, now: float) -> str:
    if not p.answer_pending:
        if now >= p.class_ends_at:
            return "请先用「上课」开始一节生物课。"
        if p.class_done:
            return "这节课的课堂活动已完成。"
    p.spend(rules.answer_cost)
    p.answer_pending = random.random() >= rules.answer_percent / 100
    if p.answer_pending:
        return f"答错了，请继续用「答题」重答，每次消耗 {rules.answer_cost} 点精力。当前精力 {p.energy}。"
    p.class_done = True
    return f"答对了！成绩 +{p.add_score(rules.answer_points)}，当前 {p.score} 分，精力 {p.energy}。"


def nap(p: Player, rules: Rules, now: float) -> str:
    if p.answer_pending:
        return "请先用「答题」重答这道问题。"
    if now >= p.class_ends_at:
        return "请先用「上课」开始一节生物课。"
    if p.class_done:
        return "这节课的课堂活动已完成。"
    recovered = min(rules.nap_energy, rules.max_energy - p.energy)
    p.energy += recovered
    p.class_done = True
    return f"你选了上课睡觉，精力 +{recovered}，当前 {p.energy}/{rules.max_energy}。"


def dictation(p: Player, rules: Rules) -> str:
    if p.signed:
        return "你已签订协议，永久免默写。"
    if p.dictation_done:
        return "今天的默写已经过关了。"
    p.spend(rules.dictation_cost)
    p.dictation_pending = random.random() >= rules.dictation_percent / 100
    if p.dictation_pending:
        return f"默写未过关，请继续用「默写」重默，每次消耗 {rules.dictation_cost} 点精力。当前精力 {p.energy}。"
    p.dictation_done = True
    p.dictation_homework += rules.dictation_homework
    reward = f"，获得 {rules.dictation_homework} 份作业" if rules.dictation_homework else ""
    return (
        f"默写过关{reward}！成绩 +{p.add_score(rules.dictation_points)}，"
        f"当前 {p.score} 分，精力 {p.energy}。"
    )


def sign(p: Player, rules: Rules, confirmed: bool) -> str:
    if p.signed:
        return "你已经签订了永久免默写协议。"
    if p.dictation_pending:
        return "还有一场默写待重默，过关后可签约。"
    if not confirmed:
        return (
            "签约后永久免默写，失去今后的默写奖励作业。"
            f"获得 {rules.bonus_days} 天「红利」，每天加 {rules.bonus_points} 分。"
            "确定后发送「/确认签约」。"
        )
    p.signed = True
    p.bonus_until = p.day + rules.bonus_days
    p.bonus_points = rules.bonus_points
    return (
        f"协议已签订。今天起连续 {rules.bonus_days} 天每天加 {p.bonus_points} 分，"
        f"今日实际 +{p.add_score(p.bonus_points)}。"
        "每日作业照常发放。"
    )


def steal_paper(p: Player, rules: Rules) -> str:
    if not p.signed:
        return "签订协议后才可以尝试偷试卷。"
    p.spend(rules.steal_cost)
    if random.random() >= rules.steal_percent / 100:
        return f"被老师发现了，没拿到试卷。当前精力 {p.energy}。"
    p.papers += 1
    return f"拿到 1 张试卷！现有 {p.papers} 张，当前精力 {p.energy}。"


def do_homework(p: Player, rules: Rules, copy: bool) -> str:
    if not p.daily_homework and not p.dictation_homework:
        return "手上没有待完成的作业。"
    if copy and p.copy_blocked_until > p.day:
        return f"「同」还剩 {p.copy_blocked_until - p.day} 天，作业只能自己写。"
    p.spend(rules.copy_cost if copy else rules.homework_cost)
    if p.daily_homework:
        p.daily_homework -= 1
    else:
        p.dictation_homework -= 1
    if copy and random.random() < rules.copy_penalty_percent / 100:
        p.copy_blocked_until = p.day + rules.copy_penalty_days
        result = f"抄完了，但触发「同」，从今天起连续 {rules.copy_penalty_days} 天只能自己写。"
    elif copy:
        result = "抄完了 1 份作业。"
    else:
        result = f"独立完成 1 份作业，成绩 +{p.add_score(rules.homework_points)}，当前 {p.score} 分。"
    return f"{result} 剩余作业 {p.daily_homework + p.dictation_homework} 份，精力 {p.energy}。"


def daily_quote(p: Player, rules: Rules) -> str:
    if not p.quote:
        p.quote = random.choice(rules.quotes)
    return f"今日生物课金句\n「{p.quote}」"


ACTIONS = {
    "状态": status,
    "上课": start_class,
    "答题": answer,
    "睡觉": nap,
    "默写": lambda p, rules, now: dictation(p, rules),
    "签订协议": lambda p, rules, now: sign(p, rules, confirmed=False),
    "确认签约": lambda p, rules, now: sign(p, rules, confirmed=True),
    "偷试卷": lambda p, rules, now: steal_paper(p, rules),
    "写作业": lambda p, rules, now: do_homework(p, rules, copy=False),
    "抄作业": lambda p, rules, now: do_homework(p, rules, copy=True),
    "金句": lambda p, rules, now: daily_quote(p, rules),
}


class Game:
    def __init__(self, save_path: Path, config: Mapping | None = None):
        self.rules = Rules.load(config or {})
        self.save_path = save_path
        with closing(sqlite3.connect(self.save_path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    PRIMARY KEY (platform, user_id)
                )
            """)

    def play(self, platform: str, user_id: str, action: str) -> str:
        handler = ACTIONS.get(action)
        if handler is None:
            return HELP
        with closing(sqlite3.connect(self.save_path)) as db, db:
            # 在读档前锁定写事务，避免并发指令重复领奖或覆盖进度。
            db.execute("BEGIN IMMEDIATE")
            now = datetime.now(GAME_TZ)
            row = db.execute(
                "SELECT state FROM players WHERE platform = ? AND user_id = ?",
                (platform, user_id),
            ).fetchone()
            if row:
                player = Player(**json.loads(row[0]))
            else:
                player = Player(score=self.rules.initial_score, energy=self.rules.max_energy)
            player.settle_day(now.date().toordinal(), self.rules)
            try:
                reply = handler(player, self.rules, now.timestamp())
            except MoveUnavailable as error:
                reply = str(error)
            db.execute(
                """
                INSERT INTO players (platform, user_id, state) VALUES (?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET state = excluded.state
                """,
                (platform, user_id, json.dumps(asdict(player), ensure_ascii=False)),
            )
        return reply
