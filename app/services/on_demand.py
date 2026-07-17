from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.content import BotCopy
from app.domain.models import SummaryKind, TelegramMembership
from app.domain.ports import (
    ChatRepository,
    ChatShareRequestRepository,
    MemberRepository,
    SummarySelectionRepository,
    TelegramGateway,
)
from app.integrations.telegram import IncomingUpdate
from app.services.summaries import SummaryService

NowFactory = Callable[[], datetime]


class OnDemandSummaryService:
    def __init__(
        self,
        *,
        summaries: SummaryService,
        telegram: TelegramGateway,
        copy: BotCopy,
        now: NowFactory,
    ) -> None:
        self.summaries = summaries
        self.telegram = telegram
        self.copy = copy
        self.now = now

    def request(
        self,
        *,
        chat_id: int,
        requester_user_id: int,
        destination_chat_id: int,
        ack_reply_markup: dict | None = None,
    ) -> None:
        self.telegram.send_message(
            destination_chat_id,
            self.copy.value("messages", "summary_accepted"),
            reply_markup=ack_reply_markup,
        )
        result = self.summaries.generate(
            chat_id,
            SummaryKind.ON_DEMAND,
            requester_user_id,
            self.now(),
        )
        if result.outcome == "preparing":
            self.telegram.send_message(
                destination_chat_id,
                self.copy.value("messages", "summary_in_progress"),
            )
            return
        if result.outcome == "insufficient_messages":
            self.telegram.send_message(
                destination_chat_id,
                self.copy.value("messages", "not_enough_messages"),
            )
            return
        self.telegram.send_message(destination_chat_id, result.text, parse_mode="HTML")


class OnDemandSummaryHandler:
    def __init__(
        self,
        *,
        chats: ChatRepository,
        members: MemberRepository,
        share_requests: ChatShareRequestRepository,
        selections: SummarySelectionRepository,
        telegram: TelegramGateway,
        summaries: SummaryService,
        copy: BotCopy,
        bot_user_id: int,
        now: NowFactory,
    ) -> None:
        self.chats = chats
        self.members = members
        self.share_requests = share_requests
        self.selections = selections
        self.telegram = telegram
        self.copy = copy
        self.bot_user_id = bot_user_id
        self.now = now
        self.requests = OnDemandSummaryService(
            summaries=summaries,
            telegram=telegram,
            copy=copy,
            now=now,
        )

    def __call__(self, update: IncomingUpdate) -> None:
        if update.callback is not None:
            self._handle_callback(update)
            return
        if update.chat_shared is not None:
            self._handle_chat_shared(update)
            return
        message = update.message
        if message is None or message.chat_type != "private" or message.user_id is None:
            return

        token = _private_start_token(message.text)
        if token is not None:
            linked = self.chats.resolve_private_link(token)
            if linked is None:
                self.telegram.send_message(
                    message.chat_id,
                    self.copy.value("messages", "private_link_invalid"),
                )
                self._show_picker(update.update_id, message.user_id, message.chat_id)
                return
            self._verify_and_deliver(
                chat_id=linked[0],
                user_id=message.user_id,
                username=message.username,
                full_name=message.full_name,
                destination_chat_id=message.chat_id,
            )
            return

        if _command_name(message.text) != "/summary":
            return
        self._handle_private_summary(
            update_id=update.update_id,
            user_id=message.user_id,
            username=message.username,
            full_name=message.full_name,
            destination_chat_id=message.chat_id,
        )

    def _handle_private_summary(
        self,
        *,
        update_id: int,
        user_id: int,
        username: str,
        full_name: str,
        destination_chat_id: int,
    ) -> None:
        groups = [
            group
            for group in self.members.linked_groups(user_id)
            if self._group_is_accessible(group[0], user_id)
        ]
        if not groups:
            self._show_picker(update_id, user_id, destination_chat_id)
            return
        if len(groups) == 1:
            self._verify_and_deliver(
                chat_id=groups[0][0],
                user_id=user_id,
                username=username,
                full_name=full_name,
                destination_chat_id=destination_chat_id,
            )
            return

        buttons = []
        current = self.now()
        for chat_id, title in groups:
            token = self.selections.get_or_create(
                user_id,
                chat_id,
                f"update:{update_id}:known-group:{chat_id}",
                current,
            )
            buttons.append(
                [
                    {
                        "text": title,
                        "callback_data": f"summary_group:{token}",
                    }
                ]
            )
        self.telegram.send_message(
            destination_chat_id,
            self.copy.value("messages", "choose_group_known"),
            reply_markup={"inline_keyboard": buttons},
        )

    def _handle_callback(self, update: IncomingUpdate) -> None:
        callback = update.callback
        if callback is None or not callback.data.startswith("summary_group:"):
            return
        token = callback.data.removeprefix("summary_group:")
        reservation = self.selections.reserve(
            token, callback.user_id, update.update_id, self.now()
        )
        state = _reservation_state(reservation)
        if state == "finalized":
            return
        self.telegram.answer_callback_query(callback.callback_id)
        if state == "unavailable" or reservation.chat_id is None:
            self.telegram.send_message(
                callback.chat_id,
                self.copy.value("messages", "group_choice_expired"),
            )
            return
        self._verify_and_deliver(
            user_id=callback.user_id,
            username="",
            full_name="",
            chat_id=reservation.chat_id,
            destination_chat_id=callback.chat_id,
        )
        self.selections.finalize(token, callback.user_id, update.update_id, self.now())

    def _handle_chat_shared(self, update: IncomingUpdate) -> None:
        shared = update.chat_shared
        if shared is None:
            return
        reservation = self.share_requests.reserve(
            shared.request_id,
            shared.user_id,
            shared.shared_chat_id,
            update.update_id,
            self.now(),
        )
        state = _reservation_state(reservation)
        if state in {"unavailable", "finalized"} or reservation.chat_id is None:
            return
        if not self.chats.is_tracked(reservation.chat_id):
            self.telegram.send_message(
                shared.private_chat_id,
                self.copy.value("messages", "selected_group_not_connected"),
            )
        else:
            self._verify_and_deliver(
                chat_id=reservation.chat_id,
                user_id=shared.user_id,
                username="",
                full_name="",
                destination_chat_id=shared.private_chat_id,
                remove_keyboard=True,
            )
        self.share_requests.finalize(
            shared.request_id, shared.user_id, update.update_id, self.now()
        )

    def _show_picker(
        self, update_id: int, user_id: int, destination_chat_id: int
    ) -> None:
        request_id = self.share_requests.get_or_create(
            user_id,
            f"update:{update_id}:native-picker",
            self.now(),
        )
        self.telegram.send_message(
            destination_chat_id,
            self.copy.value("messages", "choose_group_native"),
            reply_markup=_picker_markup(
                request_id,
                self.copy.value("buttons", "choose_group"),
            ),
        )

    def _group_is_accessible(self, chat_id: int, user_id: int) -> bool:
        bot_membership = self.telegram.get_chat_member(chat_id, self.bot_user_id)
        user_membership = self.telegram.get_chat_member(chat_id, user_id)
        self._record_membership(chat_id, user_id, user_membership)
        return bot_membership.is_admin and _membership_allowed(user_membership)

    def _verify_and_deliver(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str,
        full_name: str,
        destination_chat_id: int,
        remove_keyboard: bool = False,
    ) -> None:
        bot_membership = self.telegram.get_chat_member(chat_id, self.bot_user_id)
        if not bot_membership.is_admin:
            self.telegram.send_message(
                destination_chat_id,
                self.copy.value("messages", "group_access_unverifiable"),
            )
            return
        user_membership = self.telegram.get_chat_member(chat_id, user_id)
        self._record_membership(chat_id, user_id, user_membership)
        if not _membership_allowed(user_membership):
            self.telegram.send_message(
                destination_chat_id,
                self.copy.value("messages", "membership_not_confirmed"),
            )
            return
        self.members.remember_group(
            chat_id,
            user_id,
            username,
            full_name,
            self.now(),
        )
        self.requests.request(
            chat_id=chat_id,
            requester_user_id=user_id,
            destination_chat_id=destination_chat_id,
            ack_reply_markup={"remove_keyboard": True} if remove_keyboard else None,
        )

    def _record_membership(
        self,
        chat_id: int,
        user_id: int,
        membership: TelegramMembership,
    ) -> None:
        self.members.record_membership(
            chat_id,
            user_id,
            membership.status,
            membership.can_read,
            self.now(),
        )


def _membership_allowed(membership: TelegramMembership) -> bool:
    return membership.can_read


def _reservation_state(reservation: object) -> str:
    state = getattr(reservation, "state")
    return str(getattr(state, "value", state))


def _private_start_token(text: str) -> str | None:
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0].split("@", 1)[0].lower() != "/start" or len(parts) != 2:
        return None
    payload = parts[1]
    return payload.removeprefix("summary_") if payload.startswith("summary_") else None


def _picker_markup(request_id: int, label: str) -> dict:
    return {
        "keyboard": [
            [
                {
                    "text": label,
                    "request_chat": {
                        "request_id": request_id,
                        "chat_is_channel": False,
                        "bot_is_member": True,
                        "request_title": True,
                    },
                }
            ]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _command_name(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    return stripped.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
