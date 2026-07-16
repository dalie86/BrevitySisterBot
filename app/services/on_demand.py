from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.domain.models import SummaryKind
from app.domain.ports import (
    MemberRepository,
    SummarySelectionRepository,
    TelegramGateway,
)
from app.integrations.telegram import IncomingUpdate
from app.services.summaries import SummaryService

NowFactory = Callable[[], datetime]

ACKNOWLEDGEMENT = (
    "Запрос принят. Подготовка сводки может занять до 1–2 минут. "
    "Пожалуйста, не отправляйте команду повторно."
)
MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}


class OnDemandSummaryService:
    def __init__(
        self,
        *,
        summaries: SummaryService,
        telegram: TelegramGateway,
        now: NowFactory,
    ) -> None:
        self.summaries = summaries
        self.telegram = telegram
        self.now = now

    def request(
        self,
        *,
        chat_id: int,
        requester_user_id: int,
        destination_chat_id: int,
    ) -> None:
        self.telegram.send_message(destination_chat_id, ACKNOWLEDGEMENT)
        result = self.summaries.generate(
            chat_id,
            SummaryKind.ON_DEMAND,
            requester_user_id,
            self.now(),
        )
        if result.outcome == "preparing":
            self.telegram.send_message(
                destination_chat_id,
                "Сводка по этим сообщениям уже готовится. Повторять запрос не нужно.",
            )
            return
        if result.outcome == "insufficient_messages":
            self.telegram.send_message(
                destination_chat_id,
                "Пока недостаточно сообщений для сводки. Нужно минимум 5 сообщений за сегодня.",
            )
            return
        self.telegram.send_message(destination_chat_id, result.text)


class OnDemandSummaryHandler:
    def __init__(
        self,
        *,
        members: MemberRepository,
        selections: SummarySelectionRepository,
        telegram: TelegramGateway,
        summaries: SummaryService,
        now: NowFactory,
    ) -> None:
        self.members = members
        self.selections = selections
        self.telegram = telegram
        self.now = now
        self.requests = OnDemandSummaryService(
            summaries=summaries,
            telegram=telegram,
            now=now,
        )

    def __call__(self, update: IncomingUpdate) -> None:
        if update.callback is not None:
            self._handle_callback(update)
            return
        message = update.message
        if (
            message is None
            or message.chat_type != "private"
            or message.user_id is None
            or _command_name(message.text) != "/summary"
        ):
            return
        groups = list(self.members.linked_groups(message.user_id))
        if not groups:
            self.telegram.send_message(
                message.chat_id,
                "Сначала запросите /summary внутри нужной группы, чтобы связать её с ботом.",
            )
            return
        if len(groups) == 1:
            self._deliver_if_member(
                user_id=message.user_id,
                chat_id=groups[0][0],
                destination_chat_id=message.chat_id,
            )
            return

        buttons = []
        current = self.now()
        for chat_id, title in groups:
            token = self.selections.create(message.user_id, chat_id, current)
            buttons.append(
                [
                    {
                        "text": title,
                        "callback_data": f"summary_group:{token}",
                    }
                ]
            )
        self.telegram.send_message(
            message.chat_id,
            "Из какой группы подготовить сводку?",
            reply_markup={"inline_keyboard": buttons},
        )

    def _handle_callback(self, update: IncomingUpdate) -> None:
        callback = update.callback
        if callback is None or not callback.data.startswith("summary_group:"):
            return
        self.telegram.answer_callback_query(callback.callback_id)
        token = callback.data.removeprefix("summary_group:")
        chat_id = self.selections.resolve(token, callback.user_id, self.now())
        if chat_id is None:
            self.telegram.send_message(
                callback.chat_id,
                "Этот выбор устарел. Отправьте /summary ещё раз.",
            )
            return
        self._deliver_if_member(
            user_id=callback.user_id,
            chat_id=chat_id,
            destination_chat_id=callback.chat_id,
        )

    def _deliver_if_member(
        self,
        *,
        user_id: int,
        chat_id: int,
        destination_chat_id: int,
    ) -> None:
        status = self.telegram.get_chat_member(chat_id, user_id)
        if status not in MEMBER_STATUSES:
            self.telegram.send_message(
                destination_chat_id,
                "Сводка недоступна: вы больше не состоите в этой группе.",
            )
            return
        self.requests.request(
            chat_id=chat_id,
            requester_user_id=user_id,
            destination_chat_id=destination_chat_id,
        )


def _command_name(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    return stripped.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
