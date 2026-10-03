"""Tiny translation layer: English source strings are the keys, catalogs map them per language.

Plural strings are keyed by their English singular and list one form per plural category
(Ukrainian has three: one / few / many). Placeholders use str.format syntax, e.g. "{n}", "{name}".
"""

LANGUAGES = {"en": "English", "uk": "Українська"}
DEFAULT = "en"


def plural_index(lang, n):
    n = abs(int(n))
    if lang == "uk":
        if n % 10 == 1 and n % 100 != 11:
            return 0
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return 1
        return 2
    return 0 if n == 1 else 1


def gettext(lang, text, **kwargs):
    text = CATALOGS.get(lang, {}).get(text, text)
    return text.format(**kwargs) if kwargs else text


def ngettext(lang, singular, plural, n, **kwargs):
    forms = PLURALS.get(lang, {}).get(singular)
    if forms:
        text = forms[plural_index(lang, n)]
    else:
        text = singular if n == 1 else plural
    return text.format(n=n, **kwargs)


def js_catalog(lang):
    """Everything the browser scripts need to translate themselves."""
    return {"lang": lang, "strings": CATALOGS.get(lang, {}), "plurals": PLURALS.get(lang, {})}


MONTHS_SHORT = {
    "uk": ["січ.", "лют.", "бер.", "квіт.", "трав.", "черв.", "лип.", "серп.", "вер.", "жовт.", "лист.", "груд."],
}


UK = {
    # layout / account
    "New loan": "Нова позика",
    "Account": "Обліковий запис",
    "Log out": "Вийти",
    "Language": "Мова",
    "Ledger": "Облік",

    # login
    "Know where every card is.": "Знайте, де кожна ваша карта.",
    "Keep track of the Magic cards you lend to friends and the ones you borrow, down to the exact edition and foil.":
        "Відстежуйте карти Magic, які ви позичаєте друзям і берете в них, — аж до конкретного видання та фойлу.",
    "Button not working? Log in via telegram.org": "Кнопка не працює? Увійдіть через telegram.org",
    "Dev login": "Вхід для розробки",
    "Log in": "Увійти",
    "Username": "Ім’я користувача",
    "Telegram login could not be verified.": "Не вдалося підтвердити вхід через Telegram.",
    "Enter a valid username.": "Введіть коректне ім’я користувача.",

    # home
    "Lent out": "Позичено",
    "Borrowed": "Взято",
    "Search cards, sets or people": "Пошук карт, сетів або людей",
    "Show returned": "Показати повернуті",
    "Lent to others": "Позичено іншим",
    "Borrowed from others": "Взято в інших",
    "All returned": "Усе повернуто",
    "Return several": "Повернути кілька",
    "Add another card with {name}": "Додати ще карту з {name}",
    "Nothing lent out": "Нічого не позичено",
    "You haven’t lent any cards yet": "Ви ще не позичали карт",
    "Cards you lend to friends will show up here.": "Тут з’являться карти, які ви позичите друзям.",
    "Nothing borrowed": "Ви нічого не брали",
    "You haven’t borrowed any cards yet": "Ви ще не брали карт",
    "Cards you borrow from friends will show up here.": "Тут з’являться карти, які ви візьмете в друзів.",
    "Record a loan": "Записати позику",
    "No cards match your search.": "Жодна карта не відповідає пошуку.",

    # person page
    "Everything with {name}": "Усе з {name}",
    "Open in Telegram": "Відкрити в Telegram",
    "Lend cards": "Позичити карти",
    "Borrow cards": "Взяти карти",
    "You lent to {name}": "Ви позичили {name}",
    "You borrowed from {name}": "Ви взяли в {name}",
    "Nothing lent to {name} right now.": "Зараз у {name} немає ваших карт.",
    "You haven’t lent {name} anything yet.": "Ви ще нічого не позичали {name}.",
    "Nothing borrowed from {name} right now.": "Зараз у вас немає карт {name}.",
    "You haven’t borrowed anything from {name} yet.": "Ви ще нічого не брали в {name}.",

    # card tile
    "Returned {when}": "Повернуто {when}",
    "Got it back": "Повернули",
    "Gave it back": "Повернуто",
    "Mark as returned": "Позначити як повернуте",
    "How many came back?": "Скільки повернули?",
    "Fewer": "Менше",
    "More": "Більше",
    "of {n}": "з {n}",
    "Mark returned": "Позначити повернутими",
    "Undo return": "Скасувати повернення",
    "More actions": "Інші дії",
    "Delete this entry? This can’t be undone.": "Видалити цей запис? Цю дію не можна скасувати.",
    "Delete entry": "Видалити запис",
    "{action} with {name}? They disappear from your ledger only — {name} keeps their own copy.":
        "{action} з {name}? Їх буде видалено лише з вашого обліку — у {name} залишиться власна копія.",

    # return-several dialog
    "What did {name} give back?": "Що повернув(-ла) {name}?",
    "What did you give back to {name}?": "Що ви повернули {name}?",
    "Close": "Закрити",
    "Select everything": "Вибрати все",
    "Lent {when}": "Позичено {when}",
    "Borrowed {when}": "Взято {when}",
    "How many {name} came back": "Скільки {name} повернуто",
    "Quantity returned": "Повернута кількість",
    "Cancel": "Скасувати",
    "Select the cards that came back.": "Виберіть карти, які повернули.",
    "today, {time}": "сьогодні, {time}",

    # new loan
    "Who?": "Хто?",
    "Direction": "Напрям",
    "I lent cards": "Я позичаю",
    "I borrowed cards": "Я беру",
    "Lent to": "Кому",
    "Borrowed from": "У кого",
    "Recent people": "Нещодавні контакти",
    "Add cards": "Додайте карти",
    "Card name": "Назва карти",
    "Start typing, e.g. Lightning Bolt": "Почніть вводити, напр. Lightning Bolt",
    "Change edition": "Інше видання",
    "Edition": "Видання",
    "Filter sets": "Фільтр сетів",
    "Finish": "Покриття",
    "Quantity": "Кількість",
    "Decrease": "Зменшити",
    "Increase": "Збільшити",
    "Add to loan": "Додати до позики",
    "This loan": "Ця позика",
    "Search for a card, pick its edition and press “{button}”. Add as many as you like.":
        "Знайдіть карту, оберіть видання й натисніть «{button}». Додавайте скільки завгодно.",
    "Record loan": "Записати позику",
    "Lending to {who}": "Кому: {who}",
    "Borrowing from {who}": "У кого: {who}",
    "Loading editions…": "Завантаження видань…",
    "Couldn’t reach Scryfall. Check your connection and try again.":
        "Не вдалося зв’язатися зі Scryfall. Перевірте з’єднання й спробуйте ще раз.",
    "No paper printings found for “{name}”.": "Не знайдено паперових видань для «{name}».",
    "foil only": "лише фойл",
    "One less {name}": "На одну {name} менше",
    "One more {name}": "Ще одна {name}",
    "Remove {name}": "Прибрати {name}",
    "Remove": "Прибрати",
    "Enter their Telegram username (letters, numbers, underscores).":
        "Введіть Telegram-ім’я користувача (літери, цифри, підкреслення).",
    "Add at least one card to the loan.": "Додайте до позики хоча б одну карту.",
    "Saving…": "Збереження…",
    "Unknown card": "Невідома карта",

    # finishes
    "Non-foil": "Звичайна",
    "Foil": "Фойл",
    "Etched foil": "Етчед-фойл",

    # server-side validation
    "Couldn't reach Scryfall, try again in a moment.": "Не вдалося зв’язатися зі Scryfall, спробуйте за хвилину.",
    "A card wasn't found on Scryfall. Remove it and pick it again.":
        "Карту не знайдено на Scryfall. Приберіть її й виберіть знову.",
    "{card} ({set}) doesn't come in {finish}.": "{card} ({set}) не виходила з покриттям «{finish}».",
    "The card list was malformed, please try again.": "Список карт пошкоджено, спробуйте ще раз.",
    "Invalid finish.": "Невідоме покриття.",
    "Quantity must be between 1 and 99.": "Кількість має бути від 1 до 99.",
    "Add at least one card.": "Додайте хоча б одну карту.",
    "Choose whether you lent or borrowed the cards.": "Виберіть, чи ви позичаєте карти, чи берете їх.",
    "Enter the other person's Telegram @username.": "Введіть Telegram @ім’я іншої людини.",
    "You can't lend cards to yourself.": "Не можна позичати карти самому собі.",

    # relative dates
    "today": "сьогодні",
    "yesterday": "вчора",
}

UK_PLURAL = {
    "card": ["карта", "карти", "карт"],
    "with {n} person": ["у {n} особи", "у {n} осіб", "у {n} осіб"],
    "from {n} person": ["від {n} особи", "від {n} осіб", "від {n} осіб"],
    "{n} card out": ["{n} карта не повернута", "{n} карти не повернуті", "{n} карт не повернуто"],
    "{n} card": ["{n} карта", "{n} карти", "{n} карт"],
    "{n} printing": ["{n} видання", "{n} видання", "{n} видань"],
    "Record loan · {n} card": ["Записати позику · {n} карта", "Записати позику · {n} карти", "Записати позику · {n} карт"],
    "Mark {n} card returned": ["Позначити {n} карту повернутою", "Позначити {n} карти повернутими",
                               "Позначити {n} карт повернутими"],
    "Marked {n} card as returned.": ["{n} карту позначено повернутою.", "{n} карти позначено повернутими.",
                                     "{n} карт позначено повернутими."],
    "Loan recorded: {n} card.": ["Позику записано: {n} карта.", "Позику записано: {n} карти.",
                                 "Позику записано: {n} карт."],
    "A loan can have at most {n} different card.": ["У позиці може бути щонайбільше {n} різна карта.",
                                                    "У позиці може бути щонайбільше {n} різні карти.",
                                                    "У позиці може бути щонайбільше {n} різних карт."],
    "{n} day ago": ["{n} день тому", "{n} дні тому", "{n} днів тому"],
    "Delete {n} returned record": ["Видалити {n} повернутий запис", "Видалити {n} повернуті записи",
                                   "Видалити {n} повернутих записів"],
    "Deleted {n} returned record.": ["Видалено {n} повернутий запис.", "Видалено {n} повернуті записи.",
                                     "Видалено {n} повернутих записів."],
}

CATALOGS = {"uk": UK}
PLURALS = {"uk": UK_PLURAL}
