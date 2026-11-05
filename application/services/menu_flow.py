"""Сценарии меню «В гости» и «Вписаться»: шаги, проверки и отправка заявки.

``MenuFlowService`` — конечный автомат поверх хранилища (aiogram не нужен).
``MenuRequestRelay`` — отправка итогового сообщения в коммуну через общий
DeliveryPipeline, т. е. с Mongo-claims, маппингом для reply и статистикой.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from application.services.relay import RelayResult, RelayService, RelayTextRequest
from application.services.routing import dm_destination_allowed, resolve_destination
from domain.menu_flow import (
    GUEST, MAX_DATE_LEN, MAX_DAYS, MAX_TEXT_LEN, OTHER_DAY, STAGES, STAY, city_group,
    next_stage, parse_date, parse_days, previous_stage, when_phrase, when_word,
)
from ports.menu_flow import MenuFlowRepositoryPort
from ports.repositories import RepositoryBundle
from presentation.menu_texts import (
    BAD_DATE, BAD_DAYS, STALE_BUTTON, TEXT_TOO_LONG,
    format_guest_request, format_stay_request,
)


class MenuFlowError(Exception):
    """Ошибка шага; ``str(exc)`` можно показывать пользователю."""


class MenuFlowService:
    def __init__(self, flows: MenuFlowRepositoryPort, chats: Any) -> None:
        self._flows = flows
        self._chats = chats

    # ── состояние ───────────────────────────────────────────────────────────
    async def get(self, user_id: int) -> dict | None:
        return await self._flows.get(user_id)

    async def clear(self, user_id: int) -> None:
        await self._flows.delete(user_id)

    async def begin(self, user_id: int, kind: str, screen_message_id: int) -> dict:
        if kind not in STAGES:
            raise MenuFlowError(STALE_BUTTON)
        flow = {"kind": kind, "stage": "city", "screen_message_id": screen_message_id}
        await self._flows.save(user_id, flow)
        return flow

    async def set_screen(self, user_id: int, flow: dict, message_id: int) -> dict:
        flow = {**flow, "screen_message_id": message_id}
        await self._flows.save(user_id, flow)
        return flow

    async def _require(self, user_id: int, stage: str) -> dict:
        flow = await self._flows.get(user_id)
        if not flow or flow.get("stage") != stage:
            raise MenuFlowError(STALE_BUTTON)
        return flow

    async def _advance(self, user_id: int, flow: dict, **values: Any) -> dict:
        merged = {**flow, **values}
        merged["stage"] = next_stage(flow["kind"], flow["stage"], merged.get("when"))
        flow = merged
        await self._flows.save(user_id, flow)
        return flow

    # ── выбор кнопками ──────────────────────────────────────────────────────
    async def communes_for_city(self, city: str) -> list[dict]:
        """Коммуны города, куда можно проситься (не скрытые, не главные).

        Главные чаты — союзы коммун (Знамя, Фасад…), special — служебные: в них
        нельзя ни прийти в гости, ни вписаться, поэтому кнопок для них нет.

        Главные чаты закрыты для ЛС на уровне маршрутизации, поэтому кнопки для
        них привели бы только к ошибке доставки.
        """
        group = city_group(city)
        if not group:
            return []
        chats = await self._chats.get_chats_by_group(group)
        allowed = [c for c in chats if not c.get("hidden") and not c.get("is_chief")]
        return sorted(allowed, key=lambda c: not c.get("is_internal", False))

    async def choose_city(self, user_id: int, city: str) -> dict:
        flow = await self._require(user_id, "city")
        if not city_group(city):
            raise MenuFlowError(STALE_BUTTON)
        return await self._advance(user_id, flow, city=city)

    async def choose_commune(self, user_id: int, chat_id: int) -> dict:
        flow = await self._require(user_id, "commune")
        allowed = {c["chat_id"] for c in await self.communes_for_city(flow["city"])}
        if chat_id not in allowed:
            raise MenuFlowError(STALE_BUTTON)
        return await self._advance(user_id, flow, chat_id=chat_id)

    async def choose_when(self, user_id: int, when: str) -> dict:
        flow = await self._require(user_id, "when")
        # «В другой день» есть только у «Вписаться».
        if not (when_word(when) or (when == OTHER_DAY and flow["kind"] == STAY)):
            raise MenuFlowError(STALE_BUTTON)
        # Дата от прошлого захода на этот шаг не должна пережить смену выбора.
        return await self._advance(user_id, flow, when=when, date=None)

    async def accept_date(self, user_id: int, raw: str) -> dict:
        flow = await self._require(user_id, "date")
        try:
            date = parse_date(raw)
        except ValueError:
            raise MenuFlowError(BAD_DATE.format(limit=MAX_DATE_LEN)) from None
        return await self._advance(user_id, flow, date=date)

    async def accept_days(self, user_id: int, raw: str) -> dict:
        flow = await self._require(user_id, "days")
        try:
            days = parse_days(raw)
        except ValueError:
            raise MenuFlowError(BAD_DAYS.format(max_days=MAX_DAYS)) from None
        return await self._advance(user_id, flow, days=days)

    async def back(self, user_id: int) -> dict | None:
        """Шаг назад. None — со стартового экрана: сценарий закрыт, нужно главное меню."""
        flow = await self._flows.get(user_id)
        if not flow:
            raise MenuFlowError(STALE_BUTTON)
        stage = previous_stage(flow["kind"], flow["stage"], flow.get("when"))
        if stage is None:
            await self._flows.delete(user_id)
            return None
        flow = {**flow, "stage": stage}
        await self._flows.save(user_id, flow)
        return flow

    # ── итог ────────────────────────────────────────────────────────────────
    async def finish_text(self, user_id: int, text: str) -> tuple[dict, dict, str]:
        """Проверить заполненность и вернуть (flow, чат-цель, текст послания).

        Состояние НЕ удаляется: пользователь должен иметь возможность повторить
        отправку, если доставка не удалась. Удаляет его вызывающий код после успеха.
        """
        flow = await self._require(user_id, "text")
        body = (text or "").strip()
        if not body:
            raise MenuFlowError("Пришли текстовое сообщение")
        if len(body) > MAX_TEXT_LEN:
            raise MenuFlowError(TEXT_TOO_LONG.format(limit=MAX_TEXT_LEN))
        chat = await self._chats.get_chat_by_id(flow.get("chat_id"))
        complete = (
            chat and when_phrase(flow) and city_group(flow.get("city", ""))
            and (flow["kind"] == GUEST or flow.get("days"))
        )
        if not complete or chat.get("group") != city_group(flow["city"]) or chat.get("hidden"):
            raise MenuFlowError(STALE_BUTTON)
        return flow, chat, body


class MenuRequestRelay(RelayService):
    """Доставка готовой заявки в коммуну (тот же pipeline, что у обычных ЛС).

    Заявка и весь диалог после неё — от @username: /anon действует только на
    обычные сообщения «Коммуна! текст» и их reply-цепочки.
    """

    def _plan(self, request, actual, header):
        # Ответы в диалоге, начатом заявкой, всегда открытые (флаг наследуется).
        return replace(super()._plan(request, actual, header), open_identity=True)

    async def send_request(
        self, flow: dict, target_chat: dict, text: str, *,
        origin_chat_id: int, origin_message_id: int, user_id: int | None, visible_name: str,
    ) -> RelayResult:
        who = visible_name  # /anon на заявки «В гости»/«Вписаться» не действует
        when = when_phrase(flow)
        if flow["kind"] == STAY:
            body = format_stay_request(who, when, int(flow["days"]), text)
        else:
            body = format_guest_request(who, when, text)
        actual = await resolve_destination(self._repositories.chats, target_chat)
        if not dm_destination_allowed(target_chat, actual):
            return RelayResult(False, skipped=True)
        request = RelayTextRequest(
            origin_chat_id=origin_chat_id, origin_message_id=origin_message_id,
            target_chat=target_chat, text=text, sender_label=who, sender_user_id=user_id,
        )
        return await self._send(request, actual, body)
