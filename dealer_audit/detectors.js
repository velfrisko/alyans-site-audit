// Выполняется внутри страницы (Playwright page.evaluate). Возвращает «факты» о странице:
// всё, что потом используют проверки конверсии, карточек, мобильной версии и SEO.
() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const txt = (document.body ? document.body.innerText : '') || '';
  const low = txt.toLowerCase();
  const html = document.documentElement.outerHTML.toLowerCase();
  const visible = (el) => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };
  const aboveFold = (el) => { const r = el.getBoundingClientRect(); return r.top < vh && r.bottom > 0 && visible(el); };
  const fixedish = (el) => { let e = el; while (e && e !== document.body) { const p = getComputedStyle(e).position; if (p === 'fixed' || p === 'sticky') return true; e = e.parentElement; } return false; };

  const LEAD_RE = /(заявк|оставить|перезвон|звонок|позвонит|консультац|записат|запись|тест[- ]?драйв|забронир|бронь|резерв|узнать (цену|стоимость)|получить (предложение|цену|расч|скидк|выгод)|рассчита|расчёт|расчет|купить|оформить|в кредит|оценить|трейд|заказать|подобрать|отправить|связаться|хочу)/i;
  const clickables = [...document.querySelectorAll('a,button,[role=button],input[type=submit],[onclick]')].filter(visible);
  const ctas = clickables.filter(e => LEAD_RE.test((e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 60)));
  const onTop = (el) => { const r = el.getBoundingClientRect(); const x = Math.min(vw - 1, Math.max(0, r.left + r.width / 2)), y = Math.min(vh - 1, Math.max(0, r.top + r.height / 2)); const t = document.elementFromPoint(x, y); return !!t && (el === t || el.contains(t) || t.contains(el)); };
  const ctaFold = ctas.filter(e => aboveFold(e) && onTop(e));  // не перекрыта баннером/поп-апом

  const anchors = [...document.querySelectorAll('a[href]')];
  const hrefs = anchors.map(a => a.href);
  const tel = anchors.filter(a => a.href.startsWith('tel:'));
  const telFold = tel.filter(e => aboveFold(e) && onTop(e));
  const phoneTextFold = (() => { // телефон текстом в первом экране (даже если не кликабелен)
    const re = /(\+7|8)[\s(-]*\d{3}[\s)-]*\d{2,3}[\s-]*\d{2}[\s-]*\d{2}/;
    return [...document.querySelectorAll('header, header *, a, span, div, p')].slice(0, 4000)
      .some(e => e.children.length === 0 && re.test(e.innerText || '') && aboveFold(e));
  })();
  const msg = {
    whatsapp: hrefs.some(h => /wa\.me|whatsapp/.test(h)),
    telegram: hrefs.some(h => /t\.me\/|telegram/.test(h)),
    max: hrefs.some(h => /max\.ru/.test(h)),
    vk: hrefs.some(h => /vk\.me|vk\.com\/(im|write)/.test(h)),
    viber: hrefs.some(h => /viber:/.test(h)),
  };
  const widgets = {
    calltouch: /calltouch/.test(html), jivo: /jivo/.test(html), envybox: /envybox|callbackkiller/.test(html),
    bitrix24: /bitrix24|b24-widget|crm\.site/.test(html), talkme: /talk-me|talkme/.test(html),
    roistat: /roistat/.test(html), generic_chat: [...document.querySelectorAll('button, a, div[role=button]')].some(e => /задать вопрос|онлайн-чат|написать нам|чат с/i.test((e.innerText || '').slice(0, 40)) && visible(e)), callibri: /callibri/.test(html), marquiz: /marquiz/.test(html),
    smartcaptcha: /smartcaptcha/.test(html), recaptcha: /recaptcha/.test(html),
  };
  const analytics = {
    metrika: /mc\.yandex\.ru\/(metrika|watch)|ym\(\d+/.test(html),
    gtm: /googletagmanager/.test(html),
    vk_pixel: /vk\.com\/rtrg|top-fwz1\.mail\.ru|vk-pixel/.test(html),
  };

  // Формы: настоящие <form> и «формы» без тега (поле телефона + кнопка в одном контейнере)
  const formEls = new Set([...document.querySelectorAll('form')]);
  document.querySelectorAll('input[type=tel], input[name*=phone i], input[placeholder*="телефон" i], input[placeholder*="+7"]').forEach(i => {
    if (!i.closest('form')) { let p = i.parentElement; for (let k = 0; k < 4 && p; k++) { if (p.querySelector('button,input[type=submit]')) { formEls.add(p); break; } p = p.parentElement; } }
  });
  const labelOf = (i) => { let t = i.getAttribute('aria-label') || i.placeholder || '';
    if (!t && i.labels && i.labels.length) t = i.labels[0].innerText;
    if (!t) { let p = i.parentElement; for (let k = 0; k < 3 && p && !t; k++) { const tx = (p.innerText || '').trim(); if (tx && tx.length < 60) t = tx; p = p.parentElement; } }
    return (t || i.name || i.type || '').trim().slice(0, 30); };
  const isPhone = (i) => i.type === 'tel' || /phone|tel/i.test(i.name || '') || /телефон|\+7/i.test(labelOf(i));
  const forms = [...formEls].map(f => {
    const inputs = [...f.querySelectorAll('input,select,textarea')].filter(i => !['hidden', 'submit', 'button'].includes(i.type));
    const phone = inputs.find(isPhone);
    const btn = f.querySelector('button,input[type=submit]');
    const ft = (f.innerText || '').toLowerCase();
    return {
      visible: visible(f), above_fold: aboveFold(f),
      fields: inputs.length,
      visible_fields: inputs.filter(visible).length,
      field_names: inputs.filter(i => i.type !== 'checkbox').slice(0, 12).map(labelOf),
      has_phone: !!phone,
      phone_mask: !!phone && (/\+7|\(|_/.test(phone.placeholder || '') || !!phone.getAttribute('data-mask') || !!phone.getAttribute('maxlength') || phone.inputMode === 'tel' || phone.type === 'tel'),
      required: inputs.filter(i => i.required || i.getAttribute('aria-required') === 'true').length,
      consent: /персональн|согласи|152-фз|политик/.test(ft) || /персональн|согласи|политик/.test(((f.parentElement || {}).innerText || '').toLowerCase().slice(0, 3000))
        || !!f.querySelector('input[name*=agree i], input[name*=consent i], input[name*=policy i], input[name*=soglas i], input[name*=personal i]'),
      checkbox: !!f.querySelector('input[type=checkbox]'),
      submit_text: btn ? (btn.innerText || btn.value || '').trim().slice(0, 40) : '',
      action: f.getAttribute && (f.getAttribute('action') || ''),
      in_modal: !!f.closest('[class*=modal],[class*=popup],[role=dialog],[class*=Modal],[class*=Popup]'),
    };
  });

  // Перекрытие первого экрана фиксированными слоями (cookie-баннеры, поп-апы, виджеты)
  const overlays = [...document.querySelectorAll('body *')].filter(e => { const st = getComputedStyle(e); return (st.position === 'fixed' || st.position === 'sticky') && visible(e); })
    .filter(e => !e.closest('header') && e.tagName !== 'HEADER').map(e => { const r = e.getBoundingClientRect(); const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)); const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      return { area: w * h, top: r.top, text: (e.innerText || '').trim().slice(0, 80) }; })
    .filter(o => o.area > 0 && o.top > 60);  // шапку (top≈0) не считаем
  const ovTop = overlays.filter(o => !overlays.some(p => p !== o && p.area > o.area && p.text.includes(o.text) && o.text));
  const overlay_share = Math.min(1, ovTop.reduce((a, o) => a + o.area, 0) / (vw * vh));
  const overlay_texts = ovTop.sort((a, b) => b.area - a.area).slice(0, 3).map(o => o.text);

  // Калькуляторы
  const ranges = document.querySelectorAll('input[type=range], [class*=slider], [class*=range]').length;
  const credit_calc = (/ежемесячн|платеж от|платёж от|первоначальн|первый взнос/.test(low) && (ranges > 0 || document.querySelectorAll('input').length > 2)) || /calculator|kalkulyator|credit-calc|calc-credit/.test(html);
  const tradein_calc = /оцен(ить|ка|им) (ваш|свой|автомобил|авто)|стоимость вашего|узнать стоимость (вашего|своего)|онлайн-оценк/.test(low);

  // Изображения
  const imgs = [...document.querySelectorAll('img')];
  const bigImgs = imgs.filter(i => (i.naturalWidth || i.width) >= 300 && (i.naturalHeight || i.height) >= 200);
  const brokenImgs = imgs.filter(i => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src) && !(i.currentSrc || i.src).startsWith('data:')).map(i => i.currentSrc || i.src).slice(0, 10);
  const noAlt = imgs.filter(i => !(i.getAttribute('alt') || '').trim()).length;

  // Мобильная пригодность
  const vpMeta = (document.querySelector('meta[name=viewport]') || {}).content || '';
  const overflowX = document.documentElement.scrollWidth > vw + 4;
  const smallTaps = clickables.filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.height < 32 || r.width < 32) && (e.innerText || '').trim().length > 0; }).length;
  const stickyCta = ctas.some(fixedish) || tel.some(e => visible(e) && fixedish(e));

  // Карточка авто: анализируем окно текста от H1 (иначе цепляются цены «похожих авто» и фильтры каталога)
  const h1el = document.querySelector('h1');
  let win = txt;
  if (h1el) { const ht = (h1el.innerText || '').trim(); const i = ht ? txt.indexOf(ht) : -1; if (i >= 0) win = txt.slice(Math.max(0, i - 300), i + 3500); }
  const wlow = win.toLowerCase();
  const priceRe = /(\d{1,3}(?:[\s\u00a0\u202f]\d{3}){1,3})\s?(₽|руб)/g;
  const allPrices = [...txt.matchAll(priceRe)].map(m => parseInt(m[1].replace(/\D/g, ''), 10)).filter(v => v >= 100000 && v <= 100000000);
  const prices = [...win.matchAll(priceRe)].map(m => parseInt(m[1].replace(/\D/g, ''), 10)).filter(v => v >= 100000 && v <= 100000000);
  const specKeys = ['двигател', 'мощност', 'л.с', 'коробк', 'кпп', 'трансмисси', 'привод', 'топлив', 'объем', 'объём', 'кузов', 'цвет', 'комплектац'];
  const specs = specKeys.filter(k => wlow.includes(k));
  const vin = /\b[A-HJ-NPR-Z0-9]{17}\b/.test(win) || /\bvin\b/i.test(win);
  const year = (win.match(/год[^\d\n]{0,20}(20[0-3]\d)/i) || win.match(/\b(20[0-3]\d)\s?(г\.|год)/) || [])[1] || null;
  const mileage = (win.match(/пробег[^\d\n]{0,15}(\d{1,3}(?:[\s\u00a0]\d{3})*)/i) || win.match(/(\d{1,3}(?:[\s\u00a0]\d{3})*)\s?км\b/) || [])[1] || null;
  const avail = { in_stock: /в наличии/.test(wlow), in_transit: /в пути/.test(wlow), on_order: /под заказ/.test(wlow), sold: /\bпродан(о|а)?\b|снят с продажи|забронирован/.test(wlow) };
  const priceOnRequest = /цена по запросу|цену уточняйте|цена:?\s*по запросу/.test(wlow);
  // Галерея: уникальные изображения в верхней части карточки (до H1 + 1,5 экрана), включая миниатюры
  const limitY = (h1el ? h1el.getBoundingClientRect().bottom + window.scrollY : 0) + vh * 1.5;
  const galSrc = new Set(imgs.filter(i => { const r = i.getBoundingClientRect(); return r.top + window.scrollY < limitY && r.width >= 50 && r.height >= 35; }).map(i => (i.currentSrc || i.src).split('?')[0]));
  const slideImgs = new Set([...document.querySelectorAll('[class*=swiper] img, [class*=slick] img, [class*=gallery] img, [class*=Gallery] img, [class*=slider] img, [class*=thumb] img, [class*=photo] img, [class*=Photo] img')].map(i => (i.currentSrc || i.src || i.dataset.src || '').split('?')[0]).filter(Boolean));
  const gallery = galSrc.size >= 3 ? galSrc.size : Math.max(galSrc.size, h1el ? Math.min(slideImgs.size, 60) : 0);

  // SEO-разметка
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => { try { return JSON.parse(s.textContent); } catch (e) { return null; } }).filter(Boolean);
  const ldTypes = []; const walk = (o) => { if (!o) return; if (Array.isArray(o)) return o.forEach(walk); if (typeof o === 'object') { if (o['@type']) ldTypes.push([].concat(o['@type']).join(',')); Object.values(o).forEach(v => typeof v === 'object' && walk(v)); } }; walk(ld);
  const micro = [...document.querySelectorAll('[itemtype]')].map(e => e.getAttribute('itemtype').split('/').pop());
  const og = !!document.querySelector('meta[property="og:title"]');

  const kw = (re) => re.test(low);
  const captcha = [...document.querySelectorAll('iframe')].some(f => /captcha/i.test(f.src || '') && visible(f) && f.getBoundingClientRect().width >= 250 && f.getBoundingClientRect().height >= 180)
    || (/введите текст с картинки|я не робот|подтвердите, что запросы отправляли вы/i.test(low) && /captcha/i.test(html));
  return {
    captcha,
    viewport: { w: vw, h: vh },
    cta_count: ctas.length, cta_fold: ctaFold.length, cta_texts: [...new Set(ctas.map(e => (e.innerText || e.value || '').trim().slice(0, 40)))].slice(0, 15),
    tel_links: tel.length, tel_fold: telFold.length, phone_text_fold: phoneTextFold,
    messengers: msg, widgets, analytics,
    forms, forms_visible: forms.filter(f => f.visible).length,
    credit_calc, tradein_calc,
    mentions: {
      credit: kw(/кредит|рассрочк/), tradein: kw(/трейд-ин|трейд ин|trade-in|trade in|обмен/), testdrive: kw(/тест-драйв|тест драйв/),
      leasing: kw(/лизинг/), offers: kw(/спецпредложен|акци|скидк|выгод/), reviews: kw(/отзыв/), warranty: kw(/гаранти/),
      stock: kw(/в наличии|авто в наличии|склад/), map: !!document.querySelector('[class*=ymaps], iframe[src*="yandex.ru/map"], iframe[src*="2gis"], [class*=map]'),
      hours: kw(/ежедневно|пн[-–—]|с \d{1,2}[:.]00|\d{1,2}:00\s?[-–—]\s?\d{1,2}:00/), address: kw(/ул\.|улица|проспект|пр-т|шоссе|ш\./),
      online_booking: kw(/забронировать онлайн|онлайн-бронир|бронирование онлайн|купить онлайн/),
      delivery: kw(/доставк/),
    },
    images: { total: imgs.length, big: bigImgs.length, broken: brokenImgs, no_alt: noAlt, gallery },
    mobile: { overlay_share: +overlay_share.toFixed(2), overlay_texts, viewport_meta: vpMeta, overflow_x: overflowX, small_taps: smallTaps, sticky_cta: stickyCta },
    car: { prices: prices.slice(0, 5), price_count: prices.length, page_price_count: allPrices.length, specs, vin, year, mileage, avail, price_on_request: priceOnRequest },
    seo: { ld_types: [...new Set(ldTypes)], micro: [...new Set(micro)], og, h2: document.querySelectorAll('h2').length, words: txt.split(/\s+/).length },
  };
}
