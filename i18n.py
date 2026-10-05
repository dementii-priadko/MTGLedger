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
    # link preview
    "Card Ledger — who has your cards": "Card Ledger — у кого ваші карти",

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
    "Log in with Telegram": "Увійти через Telegram",
    "Opens @{bot} — press Start, then tap “Log in” in the chat.":
        "Відкриє @{bot} — натисніть «Почати», а потім «Увійти» в чаті.",
    "Other ways to log in": "Інші способи входу",
    "Tap the button to log in to Card Ledger.": "Натисніть кнопку, щоб увійти в Card Ledger.",
    "Log in to Card Ledger": "Увійти в Card Ledger",
    "Hi! This bot logs you in to Card Ledger. Send /login any time to get a login link.":
        "Привіт! Цей бот допомагає увійти в Card Ledger. Надішліть /login, щоб отримати посилання для входу.",
    "Open Card Ledger": "Відкрити Card Ledger",
    "Log in": "Увійти",
    "Username": "Ім’я користувача",
    "Telegram login could not be verified.": "Не вдалося підтвердити вхід через Telegram.",
    "Enter a valid username.": "Введіть коректне ім’я користувача.",

    # home
    "Lent out": "Позичено",
    "Borrowed": "Взято",
    "Search cards, sets or people": "Пошук карт, сетів або людей",
    "Search cards or sets": "Пошук карт або сетів",
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
    "Paste a decklist": "Вставити декліст",
    "Decklist": "Декліст",
    "One card per line, like “4 Lightning Bolt”. Arena, MTGO and Moxfield exports work; a set code in brackets, like “(MH2) 267”, picks that edition, and *F* marks a foil.":
        "Одна карта на рядок, напр. «4 Lightning Bolt». Підходять експорти з Arena, MTGO і Moxfield; код сету в дужках, як «(MH2) 267», вибирає видання, а *F* позначає фойл.",
    "Add these cards": "Додати ці карти",
    "No cards found in that list. Each line should start with a number, like “4 Lightning Bolt”.":
        "У списку не знайдено карт. Кожен рядок має починатися з числа, напр. «4 Lightning Bolt».",
    "Looking up cards…": "Шукаю карти…",
    "Tap a card in the list to change its edition.": "Натисніть на карту в списку, щоб змінити видання.",
    "Not found on Scryfall: {names}": "Не знайдено на Scryfall: {names}",

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

    # missing board
    "Wanted": "Розшук",
    "Missing": "Зниклі",
    "Looking for": "Шукають",
    "Lost and wanted cards": "Зниклі й шукані карти",
    "Missing cards": "Зниклі карти",
    "Cards that people you’ve traded with lent out and lost track of. If one of them is with you, press “{button}”: it moves into your ledger as borrowed from its owner.":
        "Карти, які люди, з якими ви мінялися, комусь позичили й забули кому. Якщо одна з них у вас, натисніть «{button}» — вона з’явиться у вашому обліку як позичена в її власника.",
    "Report missing cards": "Додати зниклі карти",
    "You": "Ви",
    "It’s with me": "Вона в мене",
    "How many do you have?": "Скільки у вас?",
    "Record that you have {name}’s {card}? It goes into both your ledgers as borrowed from {name}.":
        "Записати, що {card} від {name} у вас? Карта з’явиться в обліку у вас обох як позичена в {name}.",
    "Found it": "Знайшлася",
    "How many turned up?": "Скільки знайшлося?",
    "Remove from Missing": "Прибрати з розшуку",
    "Nothing is missing": "Нічого не зникло",
    "Lent a card and can’t remember to whom? Add it here so the people you’ve traded with can check whether it’s with them.":
        "Позичили карту й не пам’ятаєте кому? Додайте її сюди, щоб люди, з якими ви мінялися, перевірили, чи не в них вона.",
    "Lost track of some cards?": "Забули, кому віддали карти?",
    "Add the cards you lent out but can’t remember to whom. People you’ve traded with see them on the Missing board, and whoever has one can mark it: it then becomes a loan in both your ledgers.":
        "Додайте карти, які ви комусь позичили, але не пам’ятаєте кому. Їх побачать у розшуку люди, з якими ви мінялися, і той, у кого карта, зможе її позначити — тоді вона стане позикою в обліку у вас обох.",
    "Add to list": "Додати до списку",
    "People you’ve traded with will see these on the Missing board.": "Їх побачать у розшуку люди, з якими ви мінялися.",
    "Add to Missing": "Додати в розшук",
    "{card} from {name} is now in your ledger.": "{card} від {name} тепер у вашому обліку.",
    "Removed from Missing.": "Прибрано з розшуку.",
    "Hide {name}’s cards": "Приховати карти {name}",
    "You won’t see {name}’s cards here any more.": "Ви більше не бачитимете тут карт {name}.",
    "Hidden people": "Приховані люди",
    "Show again": "Показувати знову",

    # wanted cards ("Looking for") and offers
    "Cards that people you’ve traded with would like to borrow. Have one to spare? Press “{button}”; once they accept, it goes into both your ledgers.":
        "Карти, які люди, з якими ви мінялися, хочуть позичити. Маєте зайву? Натисніть «{button}» — коли вони приймуть пропозицію, вона з’явиться в обліку у вас обох.",
    "I can lend it": "Можу позичити",
    "How many can you lend?": "Скільки можете позичити?",
    "Offer": "Запропонувати",
    "You offered ×{n}": "Ви запропонували ×{n}",
    "Withdraw": "Відкликати",
    "Remove {card} from your list?": "Прибрати {card} зі списку?",
    "Accept {name}’s offer? {card} goes into both your ledgers as borrowed from {name}.":
        "Прийняти пропозицію {name}? {card} з’явиться в обліку у вас обох як позичена в {name}.",
    "Accept": "Прийняти",
    "Accept {name}’s offer": "Прийняти пропозицію {name}",
    "Decline": "Відхилити",
    "Decline {name}’s offer": "Відхилити пропозицію {name}",
    "Ask to borrow cards": "Попросити карти",
    "Nobody is looking for anything": "Ніхто нічого не шукає",
    "Need a card for a deck? Ask here, and the people you’ve traded with can offer to lend it.":
        "Потрібна карта для колоди? Попросіть тут, і люди, з якими ви мінялися, зможуть її запропонувати.",
    "Looking for some cards?": "Шукаєте карти?",
    "Add the cards you’d like to borrow. People you’ve traded with see them under Looking for and can offer to lend them; when you accept an offer, it becomes a loan in both your ledgers.":
        "Додайте карти, які хочете позичити. Люди, з якими ви мінялися, побачать їх у розділі «Шукають» і зможуть запропонувати свої; коли ви приймете пропозицію, вона стане позикою в обліку у вас обох.",
    "Your request": "Ваш запит",
    "People you’ve traded with will see these under Looking for.": "Їх побачать у розділі «Шукають» люди, з якими ви мінялися.",
    "Ask around": "Запитати в друзів",
    "Offer sent. {name} will see it.": "Пропозицію надіслано. {name} її побачить.",
    "Removed from your list.": "Прибрано з вашого списку.",
    "Any version": "Будь-яке видання",
    "The lender chooses": "Обере той, хто позичає",
    "Which version can you lend?": "Яке видання можете позичити?",

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
    "{n} card missing": ["{n} карта зникла", "{n} карти зникли", "{n} карт зникло"],
    "Add {n} card to Missing": ["Додати {n} карту в розшук", "Додати {n} карти в розшук", "Додати {n} карт у розшук"],
    "{n} card wanted": ["{n} карта потрібна", "{n} карти потрібні", "{n} карт потрібно"],
    "{n} offer waiting": ["{n} пропозиція чекає", "{n} пропозиції чекають", "{n} пропозицій чекають"],
    "Ask for {n} card": ["Попросити {n} карту", "Попросити {n} карти", "Попросити {n} карт"],
    "Added {n} card to your list.": ["Додано {n} карту до вашого списку.", "Додано {n} карти до вашого списку.",
                                     "Додано {n} карт до вашого списку."],
    "Added {n} card.": ["Додано {n} карту.", "Додано {n} карти.", "Додано {n} карт."],
    "Added {n} card to Missing.": ["Додано {n} карту в розшук.", "Додано {n} карти в розшук.", "Додано {n} карт у розшук."],
}

CATALOGS = {"uk": UK}
PLURALS = {"uk": UK_PLURAL}
