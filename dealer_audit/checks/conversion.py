"""Конверсия: насколько легко посетителю оставить заявку."""
from __future__ import annotations
from ..models import Finding, PageData


def _by_kind(pages: list[PageData], kind: str) -> list[PageData]:
    return [p for p in pages if p.kind == kind and p.dom]


def run(data: dict) -> tuple[list[Finding], dict]:
    pages: list[PageData] = data["pages"]
    site = data["site"]
    F: list[Finding] = []
    home = next((p for p in pages if p.kind == "home" and p.dom), None)
    if not home:
        return [Finding("conv.home_unavailable", "conversion", "Главная страница не загрузилась у агента",
                        "Проверить доступность сайта и защиту от ботов (антибот может блокировать и часть реальных посетителей).",
                        impact=5, ease=3, confidence=0.6, evidence=[site["url"]])], {}
    d, m = home.dom, home.mobile or {}
    all_dom = [p.dom for p in pages if p.dom]
    any_ = lambda f: any(f(x) for x in all_dom)  # noqa: E731

    # --- Телефон
    if not d["tel_links"]:
        F.append(Finding("conv.no_tel_link", "conversion", "Телефон на главной не кликабелен (нет ссылки tel:)",
                         "Обернуть номер в <a href=\"tel:+7…\">: на мобильном звонок станет в одно касание.",
                         impact=4, ease=5, confidence=0.9, evidence=[home.url]))
    if m and not m.get("tel_fold") and not m.get("phone_text_fold"):
        F.append(Finding("conv.mobile_no_phone_fold", "conversion", "В мобильной версии телефона нет на первом экране",
                         "Добавить иконку звонка в шапку или закреплённую кнопку «Позвонить».",
                         impact=4, ease=4, confidence=0.75, evidence=[home.screenshots.get("mobile", home.url)]))
    # --- Перекрытие первого экрана (cookie-баннер, поп-апы)
    if m and m.get("mobile", {}).get("overlay_share", 0) >= 0.2:
        sh = m["mobile"]
        F.append(Finding("conv.mobile_overlay", "conversion", f"На мобильном всплывающие слои закрывают {round(sh['overlay_share']*100)}% первого экрана",
                         "Сделать cookie-уведомление компактной полосой (1–2 строки) внизу, не показывать одновременно несколько виджетов. Сейчас они перекрывают кнопки и предложение при первом входе.",
                         impact=4, ease=5, confidence=0.7, evidence=[home.screenshots.get("mobile", home.url)] + [f"слой: «{t}»" for t in sh.get("overlay_texts", [])[:2] if t]))
    # --- CTA в первом экране
    if d["cta_fold"] == 0:
        F.append(Finding("conv.no_cta_fold_desktop", "conversion", "На первом экране главной (desktop) нет кнопки заявки",
                         "Добавить в первый экран основное действие: «Подобрать авто», «Получить предложение», «Записаться на тест-драйв».",
                         impact=4, ease=4, confidence=0.7, evidence=[home.screenshots.get("desktop", home.url)]))
    if m and m.get("cta_fold", 0) == 0:
        F.append(Finding("conv.no_cta_fold_mobile", "conversion", "На первом экране мобильной версии нет кнопки заявки",
                         "Вынести главную кнопку в первый экран или сделать закреплённую нижнюю панель (Позвонить / Написать / Заявка).",
                         impact=4, ease=4, confidence=0.7, evidence=[home.screenshots.get("mobile", home.url)]))
    if m and not m.get("mobile", {}).get("sticky_cta"):
        F.append(Finding("conv.no_sticky_mobile", "conversion", "Нет закреплённой кнопки связи на мобильном",
                         "Закреплённая панель «Позвонить / WhatsApp / Telegram» на мобильном: телефон и мессенджер всегда в зоне большого пальца.",
                         impact=3, ease=4, confidence=0.65, evidence=[home.screenshots.get("mobile", home.url)]))
    # --- Мессенджеры
    ms = {k for x in all_dom for k, v in x["messengers"].items() if v}
    if not ms:
        F.append(Finding("conv.no_messengers", "conversion", "Нет ссылок на мессенджеры (WhatsApp / Telegram / MAX)",
                         "Добавить кнопки мессенджеров в шапку, подвал и карточку авто. Часть покупателей не звонит, а пишет.",
                         impact=4, ease=5, confidence=0.85, evidence=[home.url]))
    elif "whatsapp" not in ms and "telegram" not in ms and "max" not in ms:
        F.append(Finding("conv.weak_messengers", "conversion", f"Из мессенджеров только {', '.join(sorted(ms))}",
                         "Добавить Telegram/WhatsApp/MAX: популярнее у покупателей авто.", impact=3, ease=5, confidence=0.7, evidence=[home.url]))
    # --- Формы
    total_forms = sum(len(x["forms"]) for x in all_dom)
    lead_forms = [(p, f) for p in pages if p.dom for f in p.dom["forms"] if f["has_phone"]]
    modal = [(p, p.dom["modal_form"]) for p in pages if p.dom and p.dom.get("modal_form")]
    if not lead_forms and modal:
        p, mf = modal[0]
        if mf["visible_fields"] > 4:
            F.append(Finding("conv.long_forms", "conversion", f"Длинная форма заявки во всплывающем окне: {mf['visible_fields']} полей",
                             "Оставить 2 поля (имя, телефон); остальное менеджер уточнит в звонке.", impact=3, ease=4, confidence=0.6,
                             evidence=[f"{p.url} — кнопка «{mf.get('trigger', '')[:30]}», поля: {', '.join(mf['field_names'])}"]))
    if not lead_forms and not modal:
        F.append(Finding("conv.no_lead_forms", "conversion", "Агент не нашёл ни одной формы с полем телефона",
                         "Проверить вручную: формы могут открываться только по клику (поп-ап). Если формы нет — добавить короткую форму «Имя + Телефон» на ключевые страницы.",
                         impact=5, ease=3, confidence=0.55, evidence=[p.url for p in pages[:3]]))
    long_forms = [(p, f) for p, f in lead_forms if f["visible_fields"] > 4]
    if long_forms:
        p, f = long_forms[0]
        F.append(Finding("conv.long_forms", "conversion", f"Длинные формы заявки: до {max(x[1]['visible_fields'] for x in long_forms)} полей",
                         "Оставить 2 поля (имя, телефон); остальное менеджер уточнит в звонке. Каждое лишнее поле снижает конверсию формы.",
                         impact=3, ease=4, confidence=0.7, evidence=[f"{p.url} — поля: {', '.join(f['field_names'])}"]))
    no_consent = [(p, f) for p, f in lead_forms if not f["consent"]]
    if no_consent:
        F.append(Finding("conv.forms_no_consent", "conversion", f"Формы без текста о согласии на обработку ПД: {len(no_consent)}",
                         "Добавить согласие по 152-ФЗ (штрафы выросли в 2025 г.). Лучше текстом под кнопкой, а не обязательной галочкой: так меньше трения.",
                         impact=3, ease=5, confidence=0.6, evidence=[p.url for p, _ in no_consent[:3]]))
    no_mask = [(p, f) for p, f in lead_forms if not f["phone_mask"]]
    if no_mask:
        F.append(Finding("conv.phone_no_mask", "conversion", "Поле телефона без маски/подсказки формата",
                         "Маска +7 (___) ___-__-__ и type=tel (цифровая клавиатура на мобильном). Меньше ошибок в номерах, меньше потерянных лидов.",
                         impact=2, ease=5, confidence=0.6, evidence=[p.url for p, _ in no_mask[:3]]))
    # --- Калькуляторы и сценарии
    credit_pages = _by_kind(pages, "credit")
    if not any_(lambda x: x["credit_calc"]):
        F.append(Finding("conv.no_credit_calc", "conversion", "Нет кредитного калькулятора (ежемесячный платёж)",
                         "Добавить калькулятор платежа на главную/карточку/страницу кредита с кнопкой «Получить одобрение». Около половины новых авто в РФ покупают в кредит.",
                         impact=4, ease=3, confidence=0.7, evidence=[p.url for p in credit_pages[:1]] or [home.url]))
    if not any_(lambda x: x["mentions"]["tradein"]):
        F.append(Finding("conv.no_tradein", "conversion", "Трейд-ин не упоминается на просмотренных страницах",
                         "Добавить блок трейд-ин и форму «Оценить мой автомобиль»: это вторая по частоте точка входа в покупку.",
                         impact=4, ease=4, confidence=0.7, evidence=[home.url]))
    elif not any_(lambda x: x["tradein_calc"]):
        F.append(Finding("conv.no_tradein_valuation", "conversion", "Трейд-ин есть, но нет онлайн-оценки автомобиля",
                         "Сделать форму «Марка, модель, год, пробег → предварительная оценка за 15 минут». Это лид-магнит: человек оставляет телефон, чтобы узнать цену своего авто.",
                         impact=3, ease=3, confidence=0.65, evidence=[p.url for p in _by_kind(pages, "tradein")[:1]] or [home.url]))
    if site.get("type") in ("new",) and not any_(lambda x: x["mentions"]["testdrive"]):
        F.append(Finding("conv.no_testdrive", "conversion", "Нет записи на тест-драйв", "Добавить кнопку «Записаться на тест-драйв» в карточки моделей и на главную.",
                         impact=3, ease=4, confidence=0.6, evidence=[home.url]))
    # --- Путь к заявке из карточки авто
    cards = _by_kind(pages, "card")
    if cards:
        no_cta = [p for p in cards if p.dom["cta_count"] == 0]
        if no_cta:
            F.append(Finding("conv.card_no_cta", "conversion", f"Карточки авто без кнопки заявки: {len(no_cta)} из {len(cards)}",
                             "В каждой карточке: «Забронировать», «Купить в кредит», «Обменять по трейд-ин», «Позвонить».",
                             impact=5, ease=4, confidence=0.7, evidence=[p.url for p in no_cta[:3]]))
        mob_cards = [p for p in cards if p.mobile]
        no_fold = [p for p in mob_cards if p.mobile.get("cta_fold", 0) == 0 and not p.mobile.get("mobile", {}).get("sticky_cta")]
        if no_fold:
            F.append(Finding("conv.card_mobile_cta_below", "conversion", "В мобильной карточке авто кнопка заявки не видна без прокрутки",
                             "Закрепить панель «Цена · Кнопка заявки» внизу экрана карточки на мобильном.",
                             impact=4, ease=4, confidence=0.65, evidence=[p.screenshots.get("mobile", p.url) for p in no_fold[:2]]))
        no_credit = [p for p in cards if not (p.dom["mentions"]["credit"] or (site.get("type") == "import" and p.dom["mentions"]["leasing"]))]
        if len(no_credit) == len(cards):
            F.append(Finding("conv.card_no_credit", "conversion", "В карточках авто нет предложения кредита/платежа в месяц",
                             "Показывать «от X ₽/мес» рядом с ценой и кнопку «Рассчитать кредит».", impact=4, ease=4, confidence=0.7, evidence=[cards[0].url]))
    # --- Онлайн-консультант / обратный звонок
    w = {k for x in all_dom for k, v in x["widgets"].items() if v}
    if not w & {"jivo", "envybox", "bitrix24", "talkme", "callibri", "marquiz", "generic_chat"}:
        F.append(Finding("conv.no_chat_widget", "conversion", "Нет онлайн-чата / виджета обратного звонка",
                         "Подключить чат или обратный звонок (Jivo, Bitrix24, Envybox) с переводом в мессенджеры. Ловит тех, кто не готов звонить.",
                         impact=2, ease=4, confidence=0.5, evidence=[home.url]))
    if not any_(lambda x: x["analytics"]["metrika"]):
        F.append(Finding("conv.no_metrika", "conversion", "Не найдена Яндекс Метрика", "Без аналитики нельзя измерить эффект изменений. Установить Метрику и цели на все формы и клики по телефону.",
                         impact=4, ease=5, confidence=0.7, evidence=[home.url]))
    if not any_(lambda x: x["widgets"]["calltouch"] or x["widgets"]["roistat"] or x["widgets"]["callibri"]):
        F.append(Finding("conv.no_calltracking", "conversion", "Не найден коллтрекинг", "Без коллтрекинга не видно, какие каналы и страницы приводят звонки. Звонки — основная доля заявок у дилера.",
                         impact=2, ease=3, confidence=0.5, evidence=[home.url]))

    stats = {
        "tel_links_home": d["tel_links"], "cta_fold_desktop": d["cta_fold"], "cta_fold_mobile": m.get("cta_fold") if m else None,
        "messengers": sorted(ms), "forms_total": total_forms, "lead_forms": len(lead_forms),
        "modal_forms": len(modal), "credit_calc": any_(lambda x: x["credit_calc"]), "tradein": any_(lambda x: x["mentions"]["tradein"]),
        "tradein_valuation": any_(lambda x: x["tradein_calc"]), "widgets": sorted(w), "cta_examples": d["cta_texts"][:8],
    }
    return F, stats
