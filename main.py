import asyncio

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .game import Game


class BiologyLessonSimulator(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        data_dir = StarTools.get_data_dir("astrbot_plugin_biology_lesson_simulator")
        self.game = Game(data_dir / "players.sqlite3", config)

    async def _play(self, event: AstrMessageEvent, action: str) -> str:
        user_id = event.get_sender_id()
        if not user_id:
            return "无法识别当前玩家。"
        return await asyncio.to_thread(
            self.game.play, event.get_platform_id(), user_id, action
        )

    @filter.command("生物帮助")
    async def help(self, event: AstrMessageEvent):
        """查看生物课模拟器的玩法。"""
        yield event.plain_result(await self._play(event, ""))

    @filter.command("生物状态")
    async def status(self, event: AstrMessageEvent):
        """查看个人游戏进度。"""
        yield event.plain_result(await self._play(event, "状态"))

    @filter.command("上课")
    async def lesson(self, event: AstrMessageEvent):
        """开始一节生物课。"""
        yield event.plain_result(await self._play(event, "上课"))

    @filter.command("答题")
    async def answer(self, event: AstrMessageEvent):
        """回答课堂问题或重答。"""
        yield event.plain_result(await self._play(event, "答题"))

    @filter.command("睡觉")
    async def nap(self, event: AstrMessageEvent):
        """用本节课恢复精力。"""
        yield event.plain_result(await self._play(event, "睡觉"))

    @filter.command("默写")
    async def dictation(self, event: AstrMessageEvent):
        """参加默写或重默。"""
        yield event.plain_result(await self._play(event, "默写"))

    @filter.command("签订协议")
    async def agreement(self, event: AstrMessageEvent):
        """查看永久免默写协议。"""
        yield event.plain_result(await self._play(event, "签订协议"))

    @filter.command("确认签约")
    async def sign(self, event: AstrMessageEvent):
        """确认签订协议。"""
        yield event.plain_result(await self._play(event, "确认签约"))

    @filter.command("偷试卷")
    async def steal(self, event: AstrMessageEvent):
        """签约后尝试获得一张试卷。"""
        yield event.plain_result(await self._play(event, "偷试卷"))

    @filter.command("写作业")
    async def homework(self, event: AstrMessageEvent):
        """独立完成一份作业。"""
        yield event.plain_result(await self._play(event, "写作业"))

    @filter.command("抄作业")
    async def copy_homework(self, event: AstrMessageEvent):
        """抄同桌的一份作业。"""
        yield event.plain_result(await self._play(event, "抄作业"))

    @filter.command("每日金句")
    async def quote(self, event: AstrMessageEvent):
        """抽取今天的生物课金句。"""
        yield event.plain_result(await self._play(event, "金句"))
