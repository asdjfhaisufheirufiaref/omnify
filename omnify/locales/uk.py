"""Ukrainian UI catalogue.

Keys are the English source strings used in the widgets (and in explicit
``tr(...)`` calls).  Untranslated strings simply stay in English rather than
breaking the UI.
"""

from __future__ import annotations

NAME = "Українська"

TRANSLATIONS = {
    # ------------------------------------------------------------------
    # Shell, navigation, Settings
    # ------------------------------------------------------------------
    "Settings": "Налаштування",
    "⚙ Settings": "⚙ Налаштування",
    "About": "Про застосунок",
    "Tools": "Інструменти",
    "System": "Система",
    "General": "Загальне",
    "Hide (timers keep running)": "Сховати (таймери продовжують працювати)",
    "Open Omnify": "Відкрити Omnify",
    "Quit": "Вийти",
    "Nothing running": "Нічого не запущено",
    "A modular 'everything in one' desktop toolbox for Linux.": (
        "Модульний настільний набір інструментів «все в одному» для Linux."
    ),
    "Appearance": "Вигляд",
    "Language": "Мова",
    "Theme mode": "Режим теми",
    "Accent colour": "Акцентний колір",
    "Dark": "Темна",
    "Light": "Світла",
    "Blue": "Синій",
    "Purple": "Фіолетовий",
    "Green": "Зелений",
    "Orange": "Помаранчевий",
    "Pink": "Рожевий",
    "Teal": "Бірюзовий",
    "Clear all data": "Очистити всі дані",
    "Clear all data?": "Очистити всі дані?",
    "This deletes calendars, timers, history and settings. It cannot be undone.": (
        "Це видалить календарі, таймери, історію та налаштування. "
        "Дію неможливо скасувати."
    ),
    "Delete everything": "Видалити все",
    "Data": "Дані",
    "Language applies immediately to every module.": (
        "Мова застосовується одразу до всіх модулів."
    ),
    "Language changed": "Мову змінено",
    "Settings are stored locally in ~/.local/share/omnify.": (
        "Налаштування зберігаються локально в ~/.local/share/omnify."
    ),

    # Module names and descriptions
    "Calculator": "Калькулятор",
    "Calendar": "Календар",
    "Timer": "Таймер",
    "Stopwatch": "Секундомір",
    "Task Manager": "Диспетчер задач",
    "Scientific, exact and graphing calculator with steps, history, matrices, calculus, complex numbers and unit conversion.": (
        "Науковий, точний калькулятор із графіками, покроковими "
        "розв’язками, історією, матрицями, матаналізом, комплексними "
        "числами та перетворенням одиниць."
    ),
    "Week view, habits, occupancy stats and goals.": (
        "Тижневий план, звички, статистика зайнятості та цілі."
    ),
    "Stopwatch with laps and action triggers; keeps running in the background.": (
        "Секундомір із колами та тригерами дій; працює у фоні."
    ),
    "Live CPU, RAM and GPU usage.": "Живе навантаження CPU, RAM і GPU.",
    "Processes, performance graphs, services, startup apps and users — a Windows Task Manager for Linux.": (
        "Процеси, графіки продуктивності, служби, автозапуск і "
        "користувачі — диспетчер задач для Linux."
    ),
    "Multiple countdown timers with presets, action triggers, sound and background notifications.": (
        "Кілька таймерів зворотного відліку з шаблонами, тригерами "
        "дій, звуком і фоновими сповіщеннями."
    ),

    # ------------------------------------------------------------------
    # Common controls
    # ------------------------------------------------------------------
    "Add": "Додати",
    "Cancel": "Скасувати",
    "Save": "Зберегти",
    "Delete": "Видалити",
    "Edit": "Редагувати",
    "Close": "Закрити",
    "Refresh": "Оновити",
    "Reset": "Скинути",
    "Clear": "Очистити",
    "Search": "Пошук",
    "Name": "Назва",
    "Title": "Заголовок",
    "Date": "Дата",
    "Time": "Час",
    "Start": "Старт",
    "End": "Кінець",
    "Notes": "Нотатки",
    "Description": "Опис",
    "Location": "Місце",
    "Colour": "Колір",
    "Color": "Колір",
    "Layer": "Шар",
    "Icon": "Іконка",
    "Repeat": "Повтор",
    "Reminder": "Нагадування",
    "Reminders": "Нагадування",
    "Today": "Сьогодні",
    "Yesterday": "Учора",
    "Tomorrow": "Завтра",
    "All": "Усі",
    "None": "Немає",
    "Off": "Вимкнено",
    "On": "Увімкнено",
    "Yes": "Так",
    "No": "Ні",
    "Never": "Ніколи",
    "Default": "За замовчуванням",
    "Custom": "Власне",
    "Custom…": "Власне…",
    "Apply": "Застосувати",
    "OK": "OK",
    "Test": "Перевірити",
    "Choose…": "Вибрати…",
    "Choose a sound file": "Вибрати звуковий файл",
    "Choose a start sound file": "Вибрати звук запуску",
    "No sound file available": "Немає доступного звукового файлу",
    "Escape": "Escape",
    "Percentage": "Відсоток",
    "Completed": "Виконано",
    "Upcoming": "Майбутні",
    "Past": "Минулі",
    "Missed": "Пропущено",
    "Done": "Готово",
    "Normal": "Звичайний",
    "Compact": "Компактний",
    "Roomy": "Просторий",

    # ------------------------------------------------------------------
    # Calculator
    # ------------------------------------------------------------------
    "Scientific": "Науковий",
    "Graphing": "Графіки",
    "History": "Історія",
    "Digits": "Цифри",
    "Complete:": "Доповнення:",
    "No calculations yet.\nPress Enter to evaluate.": (
        "Обчислень ще немає.\nНатисніть Enter, щоб обчислити."
    ),
    "Exact": "Точні",
    "DEG": "ГРАД",
    "RAD": "РАД",
    "Ans": "Відп.",
    "Ans =": "Відп. =",
    "Ready": "Готово",
    "Expression": "Вираз",
    "Expressions": "Вирази",
    "Parameters": "Параметри",
    "Step-by-step": "Покроково",
    "Insert": "Вставити",
    "Fit": "Вписати",
    "Export PNG": "Експорт PNG",
    "Copy result": "Копіювати результат",
    "Copy expr": "Копіювати вираз",
    "Copy graph": "Копіювати графік",
    "Clear the expression (Ctrl+L)": "Очистити вираз (Ctrl+L)",
    "Copy the displayed result": "Копіювати показаний результат",
    "Copy the expression": "Копіювати вираз",
    "Undo (Ctrl+Z)": "Скасувати (Ctrl+Z)",
    "Redo (Ctrl+Y)": "Повторити (Ctrl+Y)",
    "Show or hide the saved calculation history": (
        "Показати або сховати історію обчислень"
    ),
    "Clear all saved calculations": "Очистити всі збережені обчислення",
    "No result to copy": "Немає результату для копіювання",
    "Expression copied": "Вираз скопійовано",
    "Result copied": "Результат скопійовано",
    "History cleared": "Історію очищено",
    "The expression is empty": "Вираз порожній",
    "Calculation error:": "Помилка обчислення:",
    "Prefer exact fractions and symbolic results, with a decimal approximation": (
        "Надавати перевагу точним дробам і символьним результатам "
        "з десятковим наближенням"
    ),
    "Decimal digits in the result": "Кількість десяткових цифр у результаті",
    "Trigonometric functions use degrees": "Тригонометрія в градусах",
    "Trigonometric functions use radians": "Тригонометрія в радіанах",
    "Scientific calculator and function grapher.": (
        "Науковий калькулятор і побудовник графіків."
    ),
    "Scientific · exact results · graphing · steps": (
        "Науковий · точні результати · графіки · кроки"
    ),
    "Add a function row": "Додати рядок функції",
    "+ Add": "+ Додати",
    "Try an example": "Спробуйте приклад",
    "Curve colour": "Колір кривої",
    "Show or hide this curve": "Показати або сховати цю криву",
    "Remove this expression": "Видалити цей вираз",
    "Reset all parameter sliders to 1": (
        "Скинути всі повзунки параметрів до 1"
    ),
    "Reset the view to x ∈ [−10, 10]": "Скинути вигляд до x ∈ [−10, 10]",
    "Fit the y-range to the visible curves": (
        "Вписати діапазон y у видимі криві"
    ),
    "Save the graph as a PNG image": "Зберегти графік як PNG",
    "Copy the graph image to the clipboard": (
        "Скопіювати зображення графіка в буфер"
    ),
    "Drag to pan · Scroll to zoom · Hover to trace": (
        "Перетягніть для панорамування · Прокрутіть для масштабу · "
        "Наведіть для трасування"
    ),
    "Tip: use y = f(x), or type a letter such as a to get a parameter slider.": (
        "Порада: використовуйте y = f(x) або введіть літеру, наприклад a, "
        "щоб отримати повзунок параметра."
    ),
    "Use x as the variable; y = f(x) is optional": (
        "Використовуйте x як змінну; y = f(x) необов’язково"
    ),
    "Use y = f(x) or a function of x": "Використовуйте y = f(x) або функцію від x",
    "Use y = f(x), not an equation": "Використовуйте y = f(x), а не рівняння",
    "Use y = f(x); y cannot appear on both sides": (
        "Використовуйте y = f(x); y не може бути з обох боків"
    ),
    "Graph fitted": "Графік вписано",
    "Graph view reset": "Вигляд графіка скинуто",
    "Graph copied to the clipboard": "Графік скопійовано в буфер",
    "Graph exported to": "Графік експортовано до",
    "Export graph as PNG": "Експортувати графік як PNG",
    "Could not render graph:": "Не вдалося відтворити графік:",
    "Choose a local folder to export the PNG": (
        "Виберіть локальну папку для експорту PNG"
    ),
    "Save as template": "Зберегти як шаблон",
    "Template saved:": "Шаблон збережено:",
    "Template deleted": "Шаблон видалено",

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------
    "Pause": "Пауза",
    "Resume": "Продовжити",
    "Lap": "Коло",
    "Presets:": "Шаблони:",
    "+ Add timer": "+ Додати таймер",
    "Delete this timer": "Видалити цей таймер",
    "Delete this preset": "Видалити цей шаблон",
    "Drag to reorder timers": "Перетягніть, щоб змінити порядок",
    "Show or hide weeks and days": "Показати або сховати тижні й дні",
    "Hours": "Години",
    "Minutes": "Хвилини",
    "Seconds": "Секунди",
    "Weeks": "Тижні",
    "Days": "Дні",
    "Sound file:": "Звуковий файл:",
    "Start sound file:": "Звук запуску:",
    "Play sound on finish:": "Відтворювати звук після завершення:",
    "Play sound on start:": "Відтворювати звук під час запуску:",
    "Sound length (s, 0 = play once):": "Тривалість звуку (с, 0 = один раз):",
    "Sounds & notifications": "Звуки та сповіщення",
    "Finish notification auto-close:": "Автозакриття сповіщення:",
    "Manual (Start button)": "Вручну (кнопка «Старт»)",
    "Start automatically after an action": "Запускати автоматично після дії",
    "Start automatically:": "Запускати автоматично:",
    "After N mouse clicks (anywhere)": "Після N кліків мишею (будь-де)",
    "After N key presses (anywhere)": "Після N натискань клавіш (будь-де)",
    "At a time (delay)": "У заданий час (затримка)",
    "Arm": "Озброїти",
    "Start in (min):": "Запуск через (хв):",
    "Press a key… (Esc cancels)": "Натисніть клавішу… (Esc — скасувати)",
    "Record key…": "Записати клавішу…",
    "Key recorded:": "Клавішу записано:",
    "Key:": "Клавіша:",
    "Timer running": "Таймер працює",
    "Timer paused": "Таймер на паузі",
    "Timer finished": "Таймер завершено",
    "Timer will start in": "Таймер запуститься через",
    "Timer duration must be positive": "Тривалість таймера має бути додатною",
    "Set a timer duration first": "Спочатку задайте тривалість таймера",
    "Trigger count must be a positive int": (
        "Кількість спрацьовувань має бути додатним цілим числом"
    ),
    "Preset needs a name": "Шаблону потрібна назва",
    "Default (system alarm)": "За замовчуванням (системний сигнал)",
    "10 seconds": "10 секунд",
    "5 seconds": "5 секунд",
    "Timers keep counting while the app is closed — click/key triggers count input anywhere on the desktop.": (
        "Таймери продовжують відлік, навіть коли застосунок закрито — "
        "тригери кліків/клавіш працюють будь-де на робочому столі."
    ),

    # ------------------------------------------------------------------
    # Stopwatch
    # ------------------------------------------------------------------
    "Elapsed": "Минуло",
    "Laps": "Кола",
    "Clear laps": "Очистити кола",
    "Configure the action trigger (same kinds as the timers).": (
        "Налаштуйте тригер дії (як у таймерів)."
    ),
    "Stopwatch with laps and action triggers; keeps running in the background.": (
        "Секундомір із колами та тригерами дій; працює у фоні."
    ),
    "Start/stop after:": "Запуск/зупинка після:",
    "Stopwatch will start in": "Секундомір запуститься через",
    "Stopwatch paused": "Секундомір на паузі",
    "Stopwatch started": "Секундомір запущено",
    "Stopwatch started (time trigger)": "Секундомір запущено (за часом)",
    "Stopwatch started (clicks trigger)": "Секундомір запущено (кліки)",
    "Stopwatch started (keys trigger)": "Секундомір запущено (клавіші)",

    # ------------------------------------------------------------------
    # System Information
    # ------------------------------------------------------------------
    "System Information": "Системна інформація",
    "Hardware, software, storage, network and sensor details — an AIDA64-style system report.": (
        "Апаратне й програмне забезпечення, накопичувачі, мережа та "
        "датчики — системний звіт у стилі AIDA64."
    ),
    "Hardware": "Обладнання",
    "Storage": "Накопичувачі",
    "Software": "Програмне забезпечення",
    "Sensors": "Датчики",
    "CPU": "ЦП",
    "RAM": "ОЗП",
    "GPU": "ГП",
    "CPU cores": "Ядра ЦП",
    "CPU frequency": "Частота ЦП",
    "CPU cache": "Кеш ЦП",
    "CPU vendor": "Виробник ЦП",
    "Motherboard": "Материнська плата",
    "BIOS": "BIOS",
    "Swap": "Файл підкачки",
    "Not configured": "Не налаштовано",
    "Not detected": "Не виявлено",
    "Disk": "Диск",
    "Mount": "Точка монтування",
    "Battery": "Батарея",
    "Host name": "Ім’я хоста",
    "Operating system": "Операційна система",
    "Kernel": "Ядро",
    "Architecture": "Архітектура",
    "Uptime": "Час роботи",
    "Boot time": "Час завантаження",
    "Session type": "Тип сесії",
    "Shell": "Оболонка",
    "Locale": "Локаль",
    "Timezone": "Часовий пояс",
    "Distribution": "Дистрибутив",
    "Generated": "Створено",
    "Status": "Стан",
    "No storage devices detected": "Накопичувачів не виявлено",
    "No network interfaces detected": "Мережевих інтерфейсів не виявлено",
    "No hardware sensors detected": "Апаратних датчиків не виявлено",
    "No GPU data": "Немає даних ГП",
    "Copy report": "Копіювати звіт",
    "Save report…": "Зберегти звіт…",
    "Save system report": "Зберегти системний звіт",
    "Updated": "Оновлено",
    "Omnify System Report": "Системний звіт Omnify",
    "System report copied": "Системний звіт скопійовано",
    "Could not copy the report: {error}": (
        "Не вдалося скопіювати звіт: {error}"
    ),
    "Could not open the save dialog": "Не вдалося відкрити діалог збереження",
    "Could not save the report: {error}": (
        "Не вдалося зберегти звіт: {error}"
    ),
    "System report saved to {path}": "Системний звіт збережено: {path}",
    "{percent}% of memory used": "використано {percent}% пам’яті",
    "System Information: Summary tab": "Системна інформація: огляд",
    "System Information: Hardware tab": "Системна інформація: обладнання",
    "System Information: Storage tab": "Системна інформація: накопичувачі",
    "System Information: Network tab": "Системна інформація: мережа",
    "System Information: Software tab": "Системна інформація: програмне забезпечення",
    "System Information: Sensors tab": "Системна інформація: датчики",
    "Refresh System Information": "Оновити системну інформацію",
    "Copy system report": "Копіювати системний звіт",

    # ------------------------------------------------------------------
    # Task Manager / System
    # ------------------------------------------------------------------
    "Processes": "Процеси",
    "Performance": "Продуктивність",
    "Services": "Служби",
    "Startup": "Автозапуск",
    "Users": "Користувачі",
    "Details": "Деталі",
    "End task": "Завершити задачу",
    "End task and all children": "Завершити задачу й усі дочірні",
    "Force end": "Примусово завершити",
    "Select a process first": "Спочатку виберіть процес",
    "Search by name or command line…": "Пошук за назвою або командою…",
    "Filter by PID, name or command line…": (
        "Фільтр за PID, назвою або командою…"
    ),
    "Top processes by CPU": "Найбільше CPU",
    "Top processes by memory": "Найбільше пам’яті",
    "Top processes by VRAM": "Найбільше VRAM",
    "Mounts": "Точки монтування",
    "Not available": "Недоступно",
    "No data": "Немає даних",
    "No autostart entries": "Немає записів автозапуску",
    "No sessions found (loginctl unavailable?)": (
        "Сесій не знайдено (loginctl недоступний?)"
    ),
    "Log out": "Вийти",
    "Could not terminate session": "Не вдалося завершити сесію",
    "Failed to end PID": "Не вдалося завершити PID",
    "Permission denied: PID": "Доступ заборонено: PID",
    "systemd not available": "systemd недоступний",
    "no GPU data": "немає даних GPU",
    "Disk R/W": "Диск Ч/З",
    "Net R/W": "Мережа Ч/З",
    "Network": "Мережа",
    "Disks": "Диски",
    "Frequency": "Частота",
    "Frequency:": "Частота:",
    "Memory": "Пам’ять",
    "Load:": "Навантаження:",
    "Startup:": "Автозапуск:",
    "Desktop Entry": "Запис робочого столу",
    "Exec": "Команда",
    "Cmdline": "Команда",
    "Threads": "Потоки",
    "User": "Користувач",
    "State": "Стан",
    "Session": "Сесія",
    "SIGTERM": "SIGTERM",
    "SIGKILL": "SIGKILL",

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------
    "Week": "Тиждень",
    "Day": "День",
    "Habits": "Звички",
    "Stats": "Статистика",
    "Goals": "Цілі",
    "+ Event": "+ Подія",
    "+ Habit": "+ Звичка",
    "+ New goal": "+ Нова ціль",
    "Add event": "Додати подію",
    "+ Add event on this day": "+ Додати подію цього дня",
    "New event": "Нова подія",
    "Edit event": "Редагувати подію",
    "Delete event": "Видалити подію",
    "Duplicate event": "Дублювати подію",
    "Delete recurring event": "Видалити повторювану подію",
    "Only this event": "Лише ця подія",
    "All events in the series": "Усі події серії",
    "This and following events": "Ця та наступні події",
    "Does not repeat": "Не повторюється",
    "Daily": "Щодня",
    "Weekly": "Щотижня",
    "Monthly": "Щомісяця",
    "Yearly": "Щороку",
    "Every N days": "Кожні N днів",
    "Every N weeks": "Кожні N тижнів",
    "Every N months": "Кожні N місяців",
    "Every N years": "Кожні N років",
    "Recurring": "Повторювана",
    "All day": "Увесь день",
    "All-day": "Увесь день",
    "Start time in 24-hour format, e.g. 08:43": (
        "Час початку у 24-годинному форматі, наприклад 08:43"
    ),
    "End time in 24-hour format, e.g. 09:30": (
        "Час завершення у 24-годинному форматі, наприклад 09:30"
    ),
    "Click to pick a date from the calendar": (
        "Натисніть, щоб вибрати дату з календаря"
    ),
    "Jump to today": "Перейти до сьогодні",
    "Previous week/month": "Попередній тиждень/місяць",
    "Next week/month": "Наступний тиждень/місяць",
    "Back to the week planner": "Назад до тижневого плану",
    "Day overview": "Огляд дня",
    "Coming up": "Незабаром",
    "All events": "Усі події",
    "All layers": "Усі шари",
    "Event layers": "Шари подій",
    "Layers:": "Шари:",
    "No layer": "Без шару",
    "Layer name must not be empty": "Назва шару не може бути порожньою",
    "Layer colour": "Колір шару",
    "Gradient": "Градієнт",
    "Gradient end colour": "Кінцевий колір градієнта",
    "Animate": "Анімувати",
    "Edit layers": "Редагувати шари",
    "+ Add layer": "+ Додати шар",
    "Delete this layer": "Видалити цей шар",
    "Show layer": "Показати шар",
    "Hide layer": "Сховати шар",
    "By layer (this month)": "За шаром (цей місяць)",
    "Search and filters": "Пошук і фільтри",
    "Search titles and notes…": "Пошук у заголовках і нотатках…",
    "Filter by event layer": "Фільтр за шаром подій",
    "Filter by time scope": "Фільтр за часовим проміжком",
    "Nothing scheduled": "Нічого не заплановано",
    "This day is free. Add an event, start a habit, or jump back to the week planner.": (
        "Цей день вільний. Додайте подію, почніть звичку або "
        "поверніться до тижневого плану."
    ),
    "Nothing found. Try another phrase, layer or scope.": (
        "Нічого не знайдено. Спробуйте інший запит, шар або проміжок."
    ),
    "No habits yet. Press “+ Habit” in the header to add one (for example “Brush teeth”).": (
        "Звичок ще немає. Натисніть «+ Звичка» у заголовку, щоб додати "
        "її (наприклад, «Почистити зуби»)."
    ),
    "No goals yet. Press “+ New goal” to add one — for example “Read 12 books” or a checklist for a project.": (
        "Цілей ще немає. Натисніть «+ Нова ціль», щоб додати її — "
        "наприклад, «Прочитати 12 книг» або чекліст проєкту."
    ),
    "No templates yet. Open an event, press “Save as template”, and reuse it later.": (
        "Шаблонів ще немає. Відкрийте подію, натисніть «Зберегти як "
        "шаблон» і використайте його пізніше."
    ),
    "Keyboard shortcuts": "Клавіатурні скорочення",
    "Grid and reminders": "Сітка та нагадування",
    "Grid density": "Щільність сітки",
    "First visible hour": "Перша видима година",
    "Last visible hour": "Остання видима година",
    "Week starts on": "Тиждень починається з",
    "Default reminder": "Нагадування за замовчуванням",
    "Default reminder lead time in minutes (1 minute to 7 days)": (
        "Час нагадування за замовчуванням у хвилинах (від 1 хвилини до 7 днів)"
    ),
    "Minutes before the event starts (1 minute to 7 days)": (
        "Хвилин до початку події (від 1 хвилини до 7 днів)"
    ),
    "Reminder lead must be a whole number of minutes": (
        "Час нагадування має бути цілою кількістю хвилин"
    ),
    "Reminder lead must be between 0 and 1440 minutes": (
        "Час нагадування має бути від 0 до 1440 хвилин"
    ),
    "Title": "Заголовок",
    "Deadline": "Дедлайн",
    "Target": "Ціль",
    "Unit": "Одиниця",
    "Completion": "Виконання",
    "Add goal": "Додати ціль",
    "New goal": "Нова ціль",
    "Update goal": "Оновити ціль",
    "Delete goal": "Видалити ціль",
    "Delete this goal": "Видалити цю ціль",
    "Add goal item": "Додати пункт цілі",
    "Tick goal item": "Позначити пункт цілі",
    "Habit": "Звичка",
    "New habit": "Нова звичка",
    "Add habit": "Додати звичку",
    "Tick habit": "Позначити звичку",
    "Untick habit": "Зняти позначку звички",
    "Habits scheduled": "Заплановані звички",
    "Habit totals": "Підсумки звичок",
    "Statistics": "Статистика",
    "Templates": "Шаблони",
    "Delete template": "Видалити шаблон",
    "Reusable event presets": "Шаблони подій",
    "Start timer": "Запустити таймер",
    "Stopwatch": "Секундомір",
    "Timer": "Таймер",
    "Run command": "Виконати команду",
    "Start a timer/stopwatch or launch a program for one event.": (
        "Запустити таймер/секундомір або програму для події."
    ),
    "Event title must not be empty": "Заголовок події не може бути порожнім",
    "End time must be after the start time": (
        "Час завершення має бути пізніше часу початку"
    ),
    "Time must be HH:MM, e.g. 08:43": "Час має бути HH:MM, наприклад 08:43",
    "Time must be between 00:00 and 23:59": "Час має бути від 00:00 до 23:59",
    "Invalid date": "Неправильна дата",
    "Invalid time": "Неправильний час",
    "Invalid recurrence end date": "Неправильна дата завершення повтору",
    "Date must be YYYY-MM-DD": "Дата має бути YYYY-MM-DD",
    "Day of month must be 1..31": "День місяця має бути 1..31",
    "Custom…": "Власне…",
    "Palette colour name for an event's category layer.": (
        "Назва кольору палітри для шару категорії події."
    ),
    # ------------------------------------------------------------------
    # Calendar details / weekdays / defaults
    # ------------------------------------------------------------------
    "Layers": "Шари",
    "all-day": "увесь день",
    "All day": "Увесь день",
    "Mon": "Пн",
    "Tue": "Вт",
    "Wed": "Ср",
    "Thu": "Чт",
    "Fri": "Пт",
    "Sat": "Сб",
    "Sun": "Нд",
    "Monday": "Понеділок",
    "Tuesday": "Вівторок",
    "Wednesday": "Середа",
    "Thursday": "Четвер",
    "Friday": "П’ятниця",
    "Saturday": "Субота",
    "Sunday": "Неділя",
    "Work": "Робота",
    "Study": "Навчання",
    "Health": "Здоров’я",
    "Personal": "Особисте",
    "Rest": "Відпочинок",
    "by": "до",
    "every": "кожні",
    "period(s)": "період(ів)",
    "minutes before": "хвилин до",
    "When the event starts": "Коли починається подія",
    "This week:": "Цього тижня:",
    "Week %": "Тиждень %",
    "Occupancy — last 12 months (darker = busier)": (
        "Заповненість — останні 12 місяців (темніше = більше подій)"
    ),
    "Last 7 days — scheduled (grey) vs completed (green)": (
        "Останні 7 днів — заплановано (сірим) і виконано (зеленим)"
    ),
    "No habits yet": "Звичок ще немає",
    "No goals yet": "Цілей ще немає",
    "Nothing scheduled yet": "Нічого не заплановано",

    # ------------------------------------------------------------------
    # Timer / stopwatch dynamic states
    # ------------------------------------------------------------------
    "idle": "очікує",
    "running": "працює",
    "paused": "пауза",
    "finished": "завершено",
    "stopped": "зупинено",
    "armed": "озброєно",
    "Trigger": "Тригер",
    "⚡ Trigger": "⚡ Тригер",
    "⚡ Activate by action": "⚡ Активація дією",
    "Timer finished:": "Таймер завершено:",
    "Timer will start in {minutes} min": "Таймер запуститься через {minutes} хв",
    "Stopwatch will start in {minutes} min": (
        "Секундомір запуститься через {minutes} хв"
    ),
    "Lap {number}": "Коло {number}",
    "will start at {when}": "запуститься о {when}",
    "counting: {current} / {target}": "відлік: {current} / {target}",
    "Timer {n}": "Таймер {n}",
    "Saved preset": "Збережений шаблон",
    "Preset saved": "Шаблон збережено",

    # ------------------------------------------------------------------
    # Task Manager dynamic labels
    # ------------------------------------------------------------------
    "{count} services": "{count} служб",
    "{count} entries": "{count} записів",
    "{count} sessions": "{count} сесій",
    "{count} cores · {model}": "{count} ядер · {model}",
    "Load: -": "Навантаження: -",
    "Frequency: -": "Частота: -",
    "no sessions found (loginctl unavailable?)": (
        "сесій не знайдено (loginctl недоступний?)"
    ),
    "systemd not available": "systemd недоступний",
    "No autostart entries": "Немає записів автозапуску",
    "Not available": "Недоступно",
    "no GPU data": "немає даних GPU",
    "Could not update": "Не вдалося оновити",
    "Action failed": "Дія не вдалася",

    "(finished away)": "(завершився без нагляду)",
    "counting: {current} / {target}": "відлік: {current} / {target}",
    "counting: {current} / {target} (anywhere on the desktop)": (
        "відлік: {current} / {target} (будь-де на робочому столі)"
    ),
    "{name}: {time} left": "{name}: залишилось {time}",
    "{count} timers running — next: {name}, {time}": (
        "Активних таймерів: {count} — наступний: {name}, {time}"
    ),
    "{name} paused — {time} left": "{name} на паузі — залишилось {time}",
    "Timer '{name}' created — press Start when ready": (
        "Таймер «{name}» створено — натисніть «Старт», коли будете готові"
    ),
    "Stopwatch started ({source})": "Секундомір запущено ({source})",
    "Stopwatch paused ({source})": "Секундомір зупинено ({source})",
    "Key recorded: {combo}": "Клавішу записано: {combo}",
    "Start": "Старт",
    "actions": "дій",
    "(anywhere on the desktop)": "(будь-де на робочому столі)",

    "All events": "Усі події",
    "Recurring": "Повторювані",
    "{count} matching event": "Знайдено {count} подію",
    "{count} matching events": "Знайдено {count} подій",
    "(showing the first 300)": "(показано перші 300)",
    "by {date}": "до {date}",
    "no data": "немає даних",

    "💾 on a timer card saves it as a preset": (
        "💾 на картці таймера зберігає його як шаблон"
    ),

    # Calculator error messages (static engine errors)
    "Division by zero": "Ділення на нуль",
    "Expected a number": "Очікувалося число",
    "Invalid number": "Неправильне число",
    "Square root of a negative number": "Корінь з від’ємного числа",
    "The result is complex, not a real number": (
        "Результат комплексний, а не дійсний"
    ),
    "Power error": "Помилка піднесення до степеня",
    "Math error": "Математична помилка",
    "An empty matrix is not allowed": "Порожня матриця неприпустима",
    "Matrix rows must have the same length": (
        "Рядки матриці мають бути однакової довжини"
    ),
    "Matrix dimensions must match for addition": (
        "Розміри матриць мають збігатися для додавання"
    ),
    "Matrix dimensions must match for subtraction": (
        "Розміри матриць мають збігатися для віднімання"
    ),
    "Determinant requires a square matrix": (
        "Для визначника потрібна квадратна матриця"
    ),
    "Inverse requires a square matrix": (
        "Для оберненої матриці потрібна квадратна матриця"
    ),
    "Matrix is singular; it has no inverse": (
        "Матриця вироджена; оберненої не існує"
    ),
    "A square coefficient matrix is required": (
        "Потрібна квадратна матриця коефіцієнтів"
    ),
    "The right-hand side must be a vector": (
        "Права частина має бути вектором"
    ),
    "The right-hand side has the wrong length": (
        "Права частина має неправильну довжину"
    ),
    "This expression cannot be differentiated": (
        "Цей вираз не можна продиференціювати"
    ),
    "Ans is not available yet": "Ans ще недоступний",
    "Unary minus requires a number": "Унарний мінус потребує числа",
    "Postfix operators require a number": (
        "Постфіксні оператори потребують числа"
    ),
    "Expected a variable name, for example x": (
        "Очікувалася назва змінної, наприклад x"
    ),
    "diff expects an expression, a variable and an optional point": (
        "diff очікує вираз, змінну та необов’язкову точку"
    ),
    "The evaluation point must be a number": (
        "Точка обчислення має бути числом"
    ),
    "Integration bounds must be numbers": (
        "Межі інтегрування мають бути числами"
    ),
    "solve expects (equation, variable)": "solve очікує (рівняння, змінна)",
    "The domain bound must be a number": "Межа області має бути числом",
    "An equation can only be used with solve()": (
        "Рівняння можна використовувати лише з solve()"
    ),
    "An empty list is not allowed": "Порожній список неприпустимий",
    "Exponentiation requires numbers": (
        "Піднесення до степеня потребує чисел"
    ),
    "Modulo requires numbers": "Модуль потребує чисел",
    "Modulo requires real numbers": "Модуль потребує дійсних чисел",
    "Modulo by zero": "Модуль від нуля",
    "Factorial requires a real number": "Факторіал потребує дійсного числа",
    "Factorial is not defined for negative integers": (
        "Факторіал не визначений для від’ємних цілих"
    ),
    "Factorial is too large": "Факторіал завеликий",
    "Factorial domain error": "Помилка області факторіала",
    "tan is undefined at 90°": "tan не визначений під кутом 90°",
    "tan is undefined at this angle": "tan не визначений для цього кута",
    "Square root domain error": "Помилка області квадратного кореня",
    "The root degree must be an integer": (
        "Степінь кореня має бути цілим числом"
    ),
    "The root degree must not be zero": "Степінь кореня не може бути нулем",
    "Expression": "Вираз",

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------
    "Browser": "Браузер",
    "A standalone LibreWolf-based browser with Brave-style tracker, cookie, fingerprinting and HTTPS protections.": (
        "Окремий браузер на базі LibreWolf із захистом від трекерів, "
        "сторонніх cookie, зчитування відбитків і безпечним HTTPS."
    ),
    "Checking browser…": "Перевірка браузера…",
    "Open browser": "Відкрити браузер",
    "Private window": "Приватне вікно",
    "Search the web or enter an address…": "Пошук у мережі або адреса…",
    "Go": "Перейти",
    "Bookmarks": "Закладки",
    "History": "Історія",
    "Downloads": "Завантаження",
    "Passwords": "Паролі",
    "Extensions": "Розширення",
    "Browser settings": "Налаштування браузера",
    "The browser runs as a separate program with its own profile; Omnify only provisions and launches it.": (
        "Браузер працює окремою програмою з власним профілем; Omnify "
        "лише налаштовує його та запускає."
    ),
    "Brave-style protections": "Захист як у Brave",
    "{active}/{total} protections active": "Активних захистів: {active}/{total}",
    "Ad & tracker blocking": "Блокування реклами й трекерів",
    "Strict Enhanced Tracking Protection, cryptominer, social and email tracker lists.": (
        "Суворий захист від стеження, майнерів, соціальних і поштових "
        "трекерів."
    ),
    "Block third-party cookies": "Блокувати сторонні cookie",
    "Reject cross-site cookies and trackers while keeping first-party logins usable.": (
        "Відхиляти міжсайтові cookie й трекери, зберігаючи входи на "
        "першорядних сайтах."
    ),
    "HTTPS-only mode": "Лише HTTPS",
    "Upgrade every navigation to HTTPS and warn on sites that do not support it.": (
        "Переводити всі переходи на HTTPS і попереджати про сайти без "
        "підтримки."
    ),
    "Fingerprint resistance": "Захист від зчитування відбитків",
    "Randomise or normalise browser fingerprints, including letterboxing.": (
        "Ускладнювати або нормалізувати відбитки браузера, зокрема "
        "через letterboxing."
    ),
    "WebRTC address guard": "Захист адрес WebRTC",
    "Prevent WebRTC from exposing local IP addresses to websites.": (
        "Не дозволяти WebRTC розкривати локальні IP-адреси сайтам."
    ),
    "Encrypted DNS (DoH)": "Зашифрований DNS (DoH)",
    "Resolve names through Quad9 over HTTPS instead of plaintext DNS.": (
        "Розв’язувати імена через Quad9 по HTTPS замість відкритого DNS."
    ),
    "Global Privacy Control": "Global Privacy Control",
    "Send the GPC opt-out signal to every site that supports it.": (
        "Надсилати сигнал відмови GPC кожному сайту, що його підтримує."
    ),
    "Forget history and cookies on close": "Забувати історію й cookie після закриття",
    "Clear history, cookies, cache and sessions when the browser exits.": (
        "Очищати історію, cookie, кеш і сесії після виходу з браузера."
    ),
    "Built-in password manager": "Вбудований менеджер паролів",
    "Use the browser's encrypted local password store; no Omnify cloud.": (
        "Використовувати зашифроване локальне сховище паролів браузера; "
        "без хмар Omnify."
    ),
    "No telemetry or sponsored tiles": "Без телеметрії та рекламних плиток",
    "Disable studies, telemetry, Pocket and sponsored new-tab content.": (
        "Вимкнути дослідження, телеметрію, Pocket і рекламу на новій "
        "вкладці."
    ),
    "Apply protections": "Застосувати захист",
    "Reset to recommended": "Скинути до рекомендованих",
    "Changes are written to user.js and apply the next time the browser starts.": (
        "Зміни записуються в user.js і діють після наступного запуску "
        "браузера."
    ),
    "Proxy": "Проксі",
    "Mode": "Режим",
    "System proxy": "Системний проксі",
    "Direct connection": "Пряме з’єднання",
    "Manual proxy": "Ручний проксі",
    "Protocol": "Протокол",
    "Host": "Хост",
    "Port": "Порт",
    "Apply proxy": "Застосувати проксі",
    "Manual proxies apply to HTTP, HTTPS and WebRTC traffic.": (
        "Ручний проксі застосовується до HTTP, HTTPS і WebRTC."
    ),
    "Managed profile & launcher": "Керований профіль і запускач",
    "Search engine": "Пошукова система",
    "Used by Omnify's address/search bar; the browser also defaults to DuckDuckGo.": (
        "Використовується рядком пошуку Omnify; браузер також типово "
        "використовує DuckDuckGo."
    ),
    "Open profile folder": "Відкрити теку профілю",
    "Create app-menu launcher": "Створити запускач у меню програм",
    "Get LibreWolf": "Отримати LibreWolf",
    "The standalone launcher does not require the Omnify window: run “omnify-browser” or choose Omnify Browser from the app menu.": (
        "Окремий запускач не потребує вікна Omnify: виконайте "
        "«omnify-browser» або виберіть Omnify Browser у меню програм."
    ),
    "No supported browser found": "Сумісний браузер не знайдено",
    "Install LibreWolf or Firefox, then reopen this module.": (
        "Установіть LibreWolf або Firefox і знову відкрийте цей модуль."
    ),
    "LibreWolf detected — hardened profile ready": (
        "LibreWolf знайдено — захищений профіль готовий"
    ),
    "Zen Browser detected": "Zen Browser знайдено",
    "LibreWolf not installed — Firefox fallback is active": (
        "LibreWolf не встановлено — використовується Firefox"
    ),
    "Protections updated — restart the browser to apply": (
        "Захист оновлено — перезапустіть браузер, щоб застосувати"
    ),
    "Protections applied to the managed profile": (
        "Захист застосовано до керованого профілю"
    ),
    "Recommended protections restored": "Рекомендований захист відновлено",
    "Proxy settings applied": "Налаштування проксі застосовано",
    "No supported browser found. Install LibreWolf or Firefox.": (
        "Сумісний браузер не знайдено. Установіть LibreWolf або Firefox."
    ),
    "Opening {browser}": "Відкриття {browser}",
    "Opening a private {browser} window": "Відкриття приватного вікна {browser}",
    "Could not start the browser: {error}": "Не вдалося запустити браузер: {error}",
    "Could not open the profile folder: {error}": (
        "Не вдалося відкрити теку профілю: {error}"
    ),
    "Could not create the launcher: {error}": (
        "Не вдалося створити запускач: {error}"
    ),
    "Launcher installed: {path}": "Запускач встановлено: {path}",
    "Could not open the download page: {error}": (
        "Не вдалося відкрити сторінку завантаження: {error}"
    ),

    # ------------------------------------------------------------------
    # Alarm clock
    # ------------------------------------------------------------------
    "Alarm clock": "Будильник",
    "W": "тиж",
    "D": "дн",
    "H": "год",
    "M": "хв",
    "S": "с",
    "A persistent alarm clock with weekday repeats, custom sounds and snooze.": (
        "Постійний будильник із повтором за днями тижня, власними звуками "
        "та відкладенням."
    ),
    "+ Add alarm": "+ Додати будильник",
    "No alarms yet. Press “+ Add alarm” to create one.": (
        "Будильників ще немає. Натисніть «+ Додати будильник», щоб створити."
    ),
    "Edit alarm": "Редагувати будильник",
    "New alarm": "Новий будильник",
    "Alarm label": "Назва будильника",
    "Repeat on": "Повторювати",
    "Leave all days off for a one-time alarm.": (
        "Не вибирайте дні, якщо будильник має спрацювати один раз."
    ),
    "Snooze duration (minutes)": "Тривалість відкладення (хвилини)",
    "default (system alarm)": "типовий (системний сигнал)",
    "Sound": "Звук",
    "Alarm saved": "Будильник збережено",
    "Alarm deleted": "Будильник видалено",
    "Snooze": "Відкласти",
    "Stop": "Зупинити",
    "Snooze {minutes} min": "Відкласти на {minutes} хв",
    "Snoozed for {minutes} minutes": "Відкладено на {minutes} хвилин",
    "Snoozed until {time}": "Відкладено до {time}",
    "Alarm": "Будильник",
    "No alarms scheduled": "Немає запланованих будильників",
    "Next: {time} · {label} ({date})": "Далі: {time} · {label} ({date})",
    "Today · {time}": "Сьогодні · {time}",
    "Tomorrow · {time}": "Завтра · {time}",
    "Today": "Сьогодні",
    "Tomorrow": "Завтра",
    "Every day": "Щодня",
    "Once": "Одноразово",
    "Weekdays": "Будні",
    "Weekend": "Вихідні",
    "Alarm · {time} — {label}": "Будильник · {time} — {label}",
    "(+{count} more)": "(+{count} ще)",
    "Alarm: {label}": "Будильник: {label}",
    "Alarm hour must be between 0 and 23": (
        "Година будильника має бути від 0 до 23"
    ),
    "Alarm minute must be between 0 and 59": (
        "Хвилини будильника мають бути від 0 до 59"
    ),
    "Snooze duration must be between 1 and 60 minutes": (
        "Тривалість відкладення має бути від 1 до 60 хвилин"
    ),

    # ------------------------------------------------------------------
    # Home dashboard
    # ------------------------------------------------------------------
    "Home": "Головна",
    "Your starting point: live clock, next alarm and every Omnify tool.": (
        "Ваша стартова сторінка: поточний час, наступний будильник і всі "
        "інструменти Omnify."
    ),
    "Welcome": "Вітаємо",
    "Everything in one place. Pick a tool below or jump straight in.": (
        "Усе в одному місці. Виберіть інструмент нижче або переходьте одразу."
    ),
    "New calculation": "Нове обчислення",
    "Today's calendar": "Календар на сьогодні",
    "New timer": "Новий таймер",
    "At a glance": "Коротко",
    "Recent notes": "Останні нотатки",
    "Open Notes": "Відкрити нотатки",
    "Next alarm": "Наступний будильник",
    "Timers": "Таймери",
    "Stopwatch": "Секундомір",
    "Tools": "Інструменти",
    "Tip: use the sidebar to move between tools; your timer and stopwatch keep running in the background.": (
        "Порада: перемикайтеся між інструментами через бічну панель; "
        "таймери й секундомір працюють у фоні."
    ),
    "Good night": "Доброї ночі",
    "Good morning": "Доброго ранку",
    "Good afternoon": "Доброго дня",
    "Good evening": "Доброго вечора",
    "No timers yet": "Таймерів ще немає",
    "{running} running · {total} total": "Активних: {running} · усього: {total}",
    "Running": "Працює",
    "Paused": "Призупинено",
    "Stopped": "Зупинено",
    "Not found": "Не знайдено",
    "LibreWolf ready": "LibreWolf готовий",
    "Firefox fallback": "Резервний Firefox",
    "{time} · {label} ({date})": "{time} · {label} ({date})",
    "Scientific, exact and graphing calculator with steps, history, matrices, calculus, complex numbers and unit conversion.": (
        "Науковий, точний і графічний калькулятор із кроками, історією, "
        "матрицями, аналізом, комплексними числами й перетворенням одиниць."
    ),
    "Week view, habits, occupancy stats and goals.": (
        "Тижневий огляд, звички, статистика зайнятості та цілі."
    ),
    "Multiple countdown timers with presets, action triggers, sound and background notifications.": (
        "Кілька зворотних таймерів із шаблонами, тригерами дій, звуком і "
        "фоновими сповіщеннями."
    ),
    "Stopwatch with laps and action triggers; keeps running in the background.": (
        "Секундомір із колами й тригерами дій; працює у фоні."
    ),
    "Processes, performance graphs, services, startup apps and users — a Windows Task Manager for Linux.": (
        "Процеси, графіки продуктивності, служби, автозапуск і користувачі — "
        "диспетчер задач для Linux."
    ),

    # ------------------------------------------------------------------
    # Customization, privacy windows and standalone modules
    # ------------------------------------------------------------------
    "Custom colours, font and layout": "Власні кольори, шрифт і компонування",
    "Start from the current palette. Each swatch changes one theme token immediately; Reset restores the selected mode.": (
        "Почніть із поточної палітри. Кожен зразок одразу змінює один "
        "елемент теми; «Скинути» відновлює вибраний режим."
    ),
    "Background": "Фон",
    "Sidebar / alternate background": "Бічна панель / альтернативний фон",
    "Cards and raised surfaces": "Картки та підняті поверхні",
    "Hover background": "Фон при наведенні",
    "Text": "Текст",
    "Secondary text": "Другорядний текст",
    "Borders": "Межі",
    "Accent": "Акцент",
    "Danger": "Небезпека",
    "Success": "Успіх",
    "Warning": "Попередження",
    "Selected day background": "Фон вибраного дня",
    "Selected day text": "Текст вибраного дня",
    "Reset custom colours": "Скинути власні кольори",
    "Font and layout": "Шрифт і компонування",
    "UI font": "Шрифт інтерфейсу",
    "Corner radius (px)": "Радіус кутів (px)",
    "Sidebar position": "Позиція бічної панелі",
    "Left": "Ліворуч",
    "Right": "Праворуч",
    "Width": "Ширина",
    "Background image": "Фонове зображення",
    "Choose a background image": "Вибрати фонове зображення",
    "Image files": "Файли зображень",
    "Choose…": "Вибрати…",
    "None": "Немає",
    "Themes": "Теми",
    "Export your colours, font, radius and background as a portable JSON file; import it on another machine.": (
        "Експортуйте кольори, шрифт, радіус і фон у переносний JSON-файл; "
        "імпортуйте його на іншому комп’ютері."
    ),
    "Export theme…": "Експортувати тему…",
    "Import theme…": "Імпортувати тему…",
    "Export theme": "Експорт теми",
    "Import theme": "Імпорт теми",
    "Open data folder": "Відкрити теку даних",
    "Theme exported to {path}": "Тему експортовано в {path}",
    "Could not export the theme: {error}": "Не вдалося експортувати тему: {error}",
    "Could not read the theme: {error}": "Не вдалося прочитати тему: {error}",
    "That file is not an Omnify theme": "Цей файл не є темою Omnify",
    "Theme imported": "Тему імпортовано",
    "Could not open the data folder: {error}": (
        "Не вдалося відкрити теку даних: {error}"
    ),
    "All local data deleted": "Усі локальні дані видалено",
    "Maximum privacy": "Максимальна приватність",
    "Private windows, Shields and passwords": (
        "Приватні вікна, Shields і паролі"
    ),
    "Tor Browser is not installed — use “Get Tor Browser”.": (
        "Tor Browser не встановлено — скористайтеся «Отримати Tor Browser»."
    ),
    "Tor Browser ready — maximum-privacy window available": (
        "Tor Browser готовий — доступне вікно максимальної приватності"
    ),
    "Open Tor Browser": "Відкрити Tor Browser",
    "Get Tor Browser": "Отримати Tor Browser",
    "Tor Browser routes traffic through the Tor network and uses a uniform fingerprint. It is the strongest practical privacy window; no browser can promise absolute anonymity.": (
        "Tor Browser спрямовує трафік через мережу Tor і використовує "
        "однаковий відбиток. Це найсильніше практичне вікно приватності; "
        "жоден браузер не може гарантувати абсолютну анонімність."
    ),
    "Omnify Shields installed — {count} tracker hosts blocked": (
        "Omnify Shields встановлено — заблоковано хостів трекерів: {count}"
    ),
    "Omnify Shields is not installed — press “Reinstall Shields”.": (
        "Omnify Shields не встановлено — натисніть «Перевстановити Shields»."
    ),
    "Reinstall Shields": "Перевстановити Shields",
    "Add-ons": "Додатки",
    "Omnify Shields is a local-only companion to uBlock Origin. Its blocked-request count appears as a green badge on the browser toolbar; click the badge to pause it for one site.": (
        "Omnify Shields — локальний помічник uBlock Origin. Кількість "
        "заблокованих запитів показується зеленим значком на панелі "
        "браузера; натисніть значок, щоб призупинити його для одного сайту."
    ),
    "Passwords are encrypted in the local Firefox store. For real protection, set a Primary Password in about:logins. Without one, anyone who can read your home folder may read the store.": (
        "Паролі зашифровані в локальному сховищі Firefox. Для справжнього "
        "захисту встановіть головний пароль у about:logins. Без нього "
        "будь-хто з доступом до вашої домашньої теки може прочитати сховище."
    ),
    "Password settings": "Налаштування паролів",
    "Site refused to load or asked for a CAPTCHA? Disable Shields for that site from the toolbar badge, or use the Private window. Some sites also reject Tor by design.": (
        "Сайт не завантажується або просить CAPTCHA? Вимкніть Shields для "
        "цього сайту через значок на панелі або скористайтеся приватним "
        "вікном. Деякі сайти також навмисно блокують Tor."
    ),
    "Could not start Tor Browser: {error}": "Не вдалося запустити Tor Browser: {error}",
    "Opening Tor Browser — maximum privacy": (
        "Відкриття Tor Browser — максимальна приватність"
    ),
    "Could not open the Tor Browser page: {error}": (
        "Не вдалося відкрити сторінку Tor Browser: {error}"
    ),
    "Could not install Shields: {error}": "Не вдалося встановити Shields: {error}",
    "Omnify Shields installed — restart the browser": (
        "Omnify Shields встановлено — перезапустіть браузер"
    ),
    "Memory saver": "Економія пам’яті",
    "Restore tabs lazily and unload background tabs under memory pressure.": (
        "Ліниво відновлювати вкладки та вивантажувати фонові вкладки під "
        "тиском на пам’ять."
    ),
    "Omnify Shields": "Omnify Shields",
    "Install the local-only companion blocker and its per-tab counter.": (
        "Встановити локальний помічник-блокувальник із лічильником для вкладки."
    ),
    "Test": "Перевірити",
    "Playing a sound preview": "Відтворення попереднього прослуховування",
    "No system sound is available": "Системний звук недоступний",
    "Browser language": "Мова браузера",
    "LibreWolf default": "Типова LibreWolf",
    "English": "Англійська",
    "Українська": "Українська",
    "Get Ukrainian language pack": "Отримати український мовний пакет",
    "Ukrainian language pack missing — use “Get Ukrainian language pack”": (
        "Українського мовного пакета немає — скористайтеся «Отримати "
        "український мовний пакет»"
    ),
    "Browser language updated — restart the browser": (
        "Мову браузера оновлено — перезапустіть браузер"
    ),
    "Downloading the Ukrainian language pack…": (
        "Завантаження українського мовного пакета…"
    ),
    "Could not install the language pack: {error}": (
        "Не вдалося встановити мовний пакет: {error}"
    ),
    "Ukrainian language pack installed — restart the browser": (
        "Український мовний пакет встановлено — перезапустіть браузер"
    ),
    "Website language header": "Мова для сайтів (Accept-Language)",
    "Browser default": "Типово для браузера",
    "English (privacy default)": "Англійська (поширений вибір)",
    "Ukrainian": "Українська",
    "This only sets the Accept-Language header. “Browser default” leaves Firefox's own language list; English is a common, slightly less unique choice, while Ukrainian keeps local versions of sites. The interface language above can differ. This does not add anonymity outside Tor Browser.": (
        "Це змінює лише заголовок Accept-Language. «Типово для браузера» "
        "залишає власний список Firefox; англійська — поширений і трохи "
        "менш унікальний вибір, українська зберігає локальні версії сайтів. "
        "Мова інтерфейсу вище може відрізнятися. Поза Tor Browser це не "
        "додає анонімності."
    ),
    "Website language header updated — restart the browser": (
        "Мову для сайтів оновлено — перезапустіть браузер"
    ),
    "Switch from another browser": "Перехід з іншого браузера",
    "Copy bookmarks, history, saved passwords, cookies, extensions and open tabs from Firefox or LibreWolf. Chrome, Chromium, Brave, Edge and Vivaldi use Firefox's built-in importer because their encrypted vaults are not portable. The managed profile keeps Omnify's privacy protections either way.": (
        "Скопіюйте закладки, історію, збережені паролі, файли cookie, "
        "розширення та відкриті вкладки з Firefox або LibreWolf. Chrome, "
        "Chromium, Brave, Edge і Vivaldi використовують вбудований "
        "імпортер Firefox, бо їхні зашифровані сховища не переносяться "
        "напряму. Керований профіль у будь-якому разі зберігає захист "
        "Omnify."
    ),
    "Refresh": "Оновити",
    "Copy Firefox data": "Скопіювати дані Firefox",
    "Open built-in importer": "Відкрити вбудований імпортер",
    "In the Firefox import wizard choose Brave (or another Chromium browser) and tick every data category. Firefox decrypts Brave's passwords and cookies through the system keyring; Omnify never sees those secrets.": (
        "У вбудованому імпортері Firefox виберіть Brave (або інший "
        "Chromium-браузер) і позначте всі категорії даних. Firefox "
        "розшифровує паролі й файли cookie Brave через системний keyring; "
        "Omnify не бачить цих секретів."
    ),
    "Close the managed browser before copying. Omnify creates a timestamped backup of overwritten files. Source security preferences are not copied on purpose.": (
        "Закрийте керований браузер перед копіюванням. Omnify створить "
        "резервну копію перезаписаних файлів із позначкою часу. "
        "Налаштування безпеки джерела навмисно не копіюються."
    ),
    "No other browser profiles found": "Інших профілів браузерів не знайдено",
    "Firefox-family profile detected at {path}. Direct copy is available.": (
        "Знайдено профіль сімейства Firefox: {path}. Доступне пряме копіювання."
    ),
    "Chromium-family profile detected at {path}. Use the built-in Firefox importer for passwords and site data.": (
        "Знайдено профіль сімейства Chromium: {path}. Для паролів і даних "
        "сайтів використовуйте вбудований імпортер Firefox."
    ),
    "Choose a source browser first": "Спочатку виберіть браузер-джерело",
    "This browser must be imported with Firefox's built-in importer": (
        "Цей браузер потрібно імпортувати вбудованим імпортером Firefox"
    ),
    "This replaces bookmarks, history, passwords, cookies, forms, extensions and the session in the managed profile. A backup is created first. Close the managed browser now.": (
        "Це замінить закладки, історію, паролі, файли cookie, форми, "
        "розширення та сеанс у керованому профілі. Спочатку буде створено "
        "резервну копію. Закрийте керований браузер."
    ),
    "Import data into the managed browser?": (
        "Імпортувати дані в керований браузер?"
    ),
    "Cancel": "Скасувати",
    "Import": "Імпортувати",
    "Close the managed browser before importing data": (
        "Закрийте керований браузер перед імпортом даних"
    ),
    "Could not import browser data: {error}": (
        "Не вдалося імпортувати дані браузера: {error}"
    ),
    "Browser data imported ({count} files). Restart the browser.": (
        "Дані браузера імпортовано (файлів: {count}). Перезапустіть браузер."
    ),
    "Imported {count} items": "Імпортовано елементів: {count}",
    "Imported 1 item": "Імпортовано 1 елемент",
    "Nothing was imported": "Нічого не імпортовано",
    "Stop Omnify completely (timers are saved)": (
        "Повністю зупинити Omnify (таймери збережено)"
    ),
    "Preview": "Попередній перегляд",
    "Open Tor Browser: Tor routing plus Tor Browser's own anti-fingerprinting.": (
        "Відкрити Tor Browser: маршрутизація через Tor і власний "
        "антифінґерпринт Tor Browser."
    ),
    "Load this expression": "Завантажити цей вираз",
    "Redo (Ctrl+Shift+Z)": "Повторити (Ctrl+Shift+Z)",
    "Search and filter events (Ctrl+F)": "Пошук і фільтр подій (Ctrl+F)",
    "Week grid and reminder settings": "Тижнева сітка й налаштування нагадувань",
    "Create, rename, recolour or delete event layers": (
        "Створювати, перейменовувати, перефарбовувати або видаляти шари подій"
    ),
    "Show/hide this layer": "Показати або сховати цей шар",
    "Event templates": "Шаблони подій",
    "Layers colour-code events. Pick a palette colour or any custom colour, optionally with a static or animated gradient. Rename layers, add new ones or delete unused ones.": (
        "Шари кодують події кольором. Виберіть колір із палітри або "
        "довільний, за бажанням зі статичним чи анімованим градієнтом. "
        "Перейменовуйте шари, додавайте нові або видаляйте зайві."
    ),
    "Reminders use a desktop notification before each timed event. Per-event leads can be set in the event dialog; a lead of 0 disables the reminder for that event.": (
        "Нагадування показують сповіщення до початку події. Затримку можна "
        "задати в діалозі події; значення 0 вимикає нагадування для неї."
    ),
    "← Week": "← Тиждень",
    "Tick a habit for every day it is due. The last column is the week's completion percentage.": (
        "Позначайте звичку щодня, коли її потрібно виконати. Останній "
        "стовпець — відсоток виконання за тиждень."
    ),
    "Track a numeric target or a checklist — progress updates as you tick items.": (
        "Відстежуйте числову ціль або контрольний список — прогрес "
        "оновлюється під час позначення пунктів."
    ),
    "Add checklist item…": "Додати пункт списку…",
    "YYYY-MM-DD (optional)": "РРРР-ММ-ДД (необов’язково)",
    "What is happening?": "Що відбувається?",
    "YYYY-MM-DD": "РРРР-ММ-ДД",
    "HH:MM": "ГГ:ХХ",
    "checkable every day it repeats (tracker + statistics)": (
        "можна позначати щодня, коли вона повторюється (трекер + статистика)"
    ),
    "Timer name, e.g. Timer 1": "Назва таймера, напр. Таймер 1",
    "Command, e.g. libreoffice --writer": "Команда, напр. libreoffice --writer",
    "Delete this template": "Видалити цей шаблон",
    "Pick any custom colour": "Вибрати довільний колір",
    "Open week": "Відкрити тиждень",
    "Duplicate": "Дублювати",
    "New layer name…": "Назва нового шару…",
    "Save this timer's duration as a preset (the same name updates the existing preset)": (
        "Зберегти тривалість таймера як шаблон (той самий підпис "
        "оновлює наявний шаблон)"
    ),
    "✎ Layers": "✎ Шари",
    "utilization": "завантаження",

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------
    "Notes": "Нотатки",
    "Local notes and quick capture.": "Локальні нотатки та швидкий запис.",
    "Your notes will appear here.": "Тут з'являться ваші нотатки.",
    "New note": "Нова нотатка",
    "Search notes": "Пошук нотаток",
    "Search notes or #tag": "Пошук нотаток або #тег",
    "Filter notes by tag": "Фільтрувати нотатки за тегом",
    "All tags": "Усі теги",
    "Clear tag filter": "Скинути фільтр тегів",
    "No tags yet": "Тегів ще немає",
    "Manage tags…": "Керувати тегами…",
    "Manage tags": "Керування тегами",
    "Rename a tag everywhere or remove it from all notes.": "Перейменуйте тег у всіх нотатках або видаліть його з них.",
    "Tag": "Тег",
    "New tag name": "Нова назва тегу",
    "Delete tag": "Видалити тег",
    "Delete tag?": "Видалити тег?",
    "The tag will be removed from {count} notes.": "Тег буде видалено з нотаток: {count}.",
    "New tag name is empty": "Нова назва тегу порожня",
    "Could not rename tag: {error}": "Не вдалося перейменувати тег: {error}",
    "Could not delete tag: {error}": "Не вдалося видалити тег: {error}",
    "No notes changed": "Жодної нотатки не змінено",
    "Renamed in {count} notes": "Перейменовано в нотатках: {count}",
    "Tag renamed in {count} notes": "Тег перейменовано в нотатках: {count}",
    "Tag removed from {count} notes": "Тег видалено з нотаток: {count}",
    "Untitled": "Без назви",
    "More actions": "Більше дій",
    "Pin note": "Закріпити нотатку",
    "Unpin note": "Відкріпити нотатку",
    "Note pinned": "Нотатку закріплено",
    "Note unpinned": "Нотатку відкріплено",
    "Saving…": "Збереження…",
    "Saved": "Збережено",
    "{words} words · {chars} characters": "Слів: {words} · символів: {chars}",
    "0 words · 0 characters": "0 слів · 0 символів",
    "Insert image": "Вставити зображення",
    "Insert image (Ctrl+V)": "Вставити зображення (Ctrl+V)",
    "Image files": "Файли зображень",
    "All files": "Усі файли",
    "Choose an image from a local folder": "Виберіть зображення з локальної папки",
    "Could not insert image: {error}": "Не вдалося вставити зображення: {error}",
    "Image inserted: {name}": "Зображення вставлено: {name}",
    "Could not save note: {error}": "Не вдалося зберегти нотатку: {error}",
    "All notes": "Усі нотатки",
    "Unfiled": "Без папки",
    "Tags": "Теги",
    "Filter and manage folders": "Фільтр і керування папками",
    "New folder": "Нова папка",
    "Folder name": "Назва папки",
    "Create folder": "Створити папку",
    "Rename folder": "Перейменувати папку",
    "New folder name": "Нова назва папки",
    "Rename": "Перейменувати",
    "Folder renamed. Moved notes: {count}": "Папку перейменовано. Перенесено нотаток: {count}",
    "Could not rename folder: {error}": "Не вдалося перейменувати папку: {error}",
    "Delete current folder": "Видалити поточну папку",
    "Folder created: {name}": "Папку створено: {name}",
    "Could not create folder: {error}": "Не вдалося створити папку: {error}",
    "Delete folder?": "Видалити папку?",
    "Notes in this folder will be moved to Unfiled. The folder itself will be removed.": (
        "Нотатки з цієї папки буде перенесено в «Без папки». "
        "Саму папку буде видалено."
    ),
    "Delete folder": "Видалити папку",
    "Could not delete folder: {error}": "Не вдалося видалити папку: {error}",
    "Folder deleted. Moved notes: {count}": "Папку видалено. Перенесено нотаток: {count}",
    "Move note to folder": "Перемістити нотатку в папку",
    "Move note to folder: {folder}": "Перемістити нотатку в папку: {folder}",
    "Could not move note: {error}": "Не вдалося перемістити нотатку: {error}",
    "Note moved to: {folder}": "Нотатку переміщено: {folder}",
    "Preview Markdown": "Попередній перегляд Markdown",
    "Insert checklist": "Вставити чекліст",
    "Insert table": "Вставити таблицю",
    "Bold (Ctrl+B)": "Жирний (Ctrl+B)",
    "Italic (Ctrl+I)": "Курсив (Ctrl+I)",
    "Strikethrough": "Закреслений",
    "Heading": "Заголовок",
    "Bullet list": "Маркований список",
    "Numbered list": "Нумерований список",
    "Quote": "Цитата",
    "Inline code": "Код у рядку",
    "Link (Ctrl+K)": "Посилання (Ctrl+K)",
    "Import Markdown files…": "Імпортувати Markdown-файли…",
    "Import Markdown files": "Імпортувати Markdown-файли",
    "Markdown files": "Markdown-файли",
    "Imported Markdown files: {count}": "Імпортовано Markdown-файлів: {count}",
    "Could not import Markdown: {error}": "Не вдалося імпортувати Markdown: {error}",
    "Export as HTML…": "Експортувати як HTML…",
    "Export as PDF…": "Експортувати як PDF…",
    "Export as Markdown…": "Експортувати як Markdown…",
    "Export all notes as Markdown…": "Експортувати всі нотатки як Markdown…",
    "Exported note to {path}": "Нотатку експортовано до {path}",
    "Export all notes as HTML…": "Експортувати всі нотатки як HTML…",
    "Choose export folder": "Виберіть теку для експорту",
    "Could not export notes: {error}": "Не вдалося експортувати нотатки: {error}",
    "Exported notes: {count} to {path}": "Експортовано нотаток: {count} у {path}",
    "Backup notes": "Створити резервну копію",
    "Restore backup…": "Відновити з резервної копії…",
    "Restore backup": "Відновити резервну копію",
    "Restore backup?": "Відновити з резервної копії?",
    "Notes from the backup will be merged with current notes. A safety copy will be created first.": (
        "Нотатки з архіву буде додано до поточних. Спочатку буде створено "
        "страхувальну копію."
    ),
    "Could not restore backup: {error}": "Не вдалося відновити резервну копію: {error}",
    "Restored notes: {count}": "Відновлено нотаток: {count}",
    "ZIP archives": "ZIP-архіви",
    "Sync notes to folder…": "Синхронізувати нотатки в папку…",
    "Two-way sync with folder…": "Двостороння синхронізація з папкою…",
    "Choose two-way sync folder": "Виберіть папку для двосторонньої синхронізації",
    "Notes merged. Imported: {imported}, updated: {updated}, exported: {exported}": (
        "Нотатки об'єднано. Імпортовано: {imported}, оновлено: {updated}, "
        "експортовано: {exported}"
    ),
    "Delete note": "Видалити нотатку",
    "Delete note?": "Видалити нотатку?",
    "This will delete the note and its images. This cannot be undone.": (
        "Буде видалено нотатку та її зображення. Дію неможливо скасувати."
    ),
    "Note deleted": "Нотатку видалено",
    "Could not delete note: {error}": "Не вдалося видалити нотатку: {error}",
    "Move to Trash": "Перемістити в кошик",
    "Move note to Trash?": "Перемістити нотатку в кошик?",
    "The note and its images will be moved to Trash. You can restore it later.": (
        "Нотатку та її зображення буде переміщено в кошик. Їх можна "
        "відновити пізніше."
    ),
    "Note moved to Trash": "Нотатку переміщено в кошик",
    "Could not move note to Trash: {error}": (
        "Не вдалося перемістити нотатку в кошик: {error}"
    ),
    "Trash…": "Кошик…",
    "Trash": "Кошик",
    "Empty Trash": "Очистити кошик",
    "Trash is empty.": "Кошик порожній.",
    "Duplicate note": "Створити копію нотатки",
    "Note duplicated": "Нотатку скопійовано",
    "Could not duplicate note: {error}": "Не вдалося скопіювати нотатку: {error}",
    "New from template…": "Створити з шаблону…",
    "New from template": "Створити з шаблону",
    "The note will be created in the current folder.": (
        "Нотатку буде створено в поточній папці."
    ),
    "Open today's journal": "Відкрити щоденник на сьогодні",
    "Today's journal": "Щоденник на сьогодні",
    "No notes yet": "Нотаток ще немає",
    "{count} notes": "Нотаток: {count}",
    "Journal": "Щоденник",
    "Find and replace…": "Знайти й замінити…",
    "Find and replace": "Знайти й замінити",
    "Find": "Знайти",
    "Replace": "Замінити",
    "Match case": "Враховувати регістр",
    "Find next": "Знайти далі",
    "Replace all": "Замінити все",
    "Text not found": "Текст не знайдено",
    "Match found": "Збіг знайдено",
    "Nothing to replace": "Немає що замінювати",
    "Replaced: {count}": "Замінено: {count}",
    "Daily plan": "План на день",
    "Priorities": "Пріоритети",
    "Schedule": "Розклад",
    "Meeting notes": "Нотатки зустрічі",
    "Attendees": "Учасники",
    "Agenda": "Порядок денний",
    "Decisions": "Рішення",
    "Action items": "Завдання",
    "Study notes": "Нотатки навчання",
    "Key ideas": "Ключові ідеї",
    "Questions": "Запитання",
    "Summary": "Підсумок",
    "Weekly review": "Тижневий огляд",
    "Wins": "Успіхи",
    "Challenges": "Труднощі",
    "Next steps": "Наступні кроки",
    "Plan": "План",
    "Save version": "Зберегти версію",
    "Version saved": "Версію збережено",
    "Could not save version": "Не вдалося зберегти версію",
    "Could not save version: {error}": "Не вдалося зберегти версію: {error}",
    "Version history…": "Історія версій…",
    "Version history": "Історія версій",
    "No saved versions yet.": "Збережених версій ще немає.",
    "Empty note": "Порожня нотатка",
    "Restore version": "Відновити версію",
    "Restore version?": "Відновити цю версію?",
    "The current note will be saved to history first, then this version will be restored.": (
        "Спочатку поточну нотатку буде збережено в історію, а потім "
        "відновлено цю версію."
    ),
    "Version restored": "Версію відновлено",
    "Could not restore version: {error}": (
        "Не вдалося відновити версію: {error}"
    ),
    "Delete version": "Видалити версію",
    "Delete version?": "Видалити цю версію?",
    "This saved version will be deleted permanently.": (
        "Цю збережену версію буде видалено назавжди."
    ),
    "Version deleted": "Версію видалено",
    "Could not delete version: {error}": "Не вдалося видалити версію: {error}",
    "Clear history": "Очистити історію",
    "Clear history?": "Очистити історію?",
    "All saved versions of this note will be deleted. This cannot be undone.": (
        "Усі збережені версії цієї нотатки буде видалено. Дію неможливо "
        "скасувати."
    ),
    "History cleared: {count}": "Історію очищено. Видалено версій: {count}",
    "Could not clear history: {error}": "Не вдалося очистити історію: {error}",
    "Restore": "Відновити",
    "Delete permanently": "Видалити назавжди",
    "Delete permanently?": "Видалити назавжди?",
    "The note and its images will be deleted permanently. This cannot be undone.": (
        "Нотатку та її зображення буде видалено назавжди. Дію неможливо "
        "скасувати."
    ),
    "Note permanently deleted": "Нотатку видалено назавжди",
    "Could not delete note permanently: {error}": (
        "Не вдалося видалити нотатку назавжди: {error}"
    ),
    "Note restored": "Нотатку відновлено",
    "Could not restore note: {error}": "Не вдалося відновити нотатку: {error}",
    "Empty Trash?": "Очистити кошик?",
    "All notes in Trash will be deleted permanently. This cannot be undone.": (
        "Усі нотатки з кошика буде видалено назавжди. Дію неможливо "
        "скасувати."
    ),
    "Trash emptied: {count}": "Кошик очищено. Видалено нотаток: {count}",
    "Could not empty Trash: {error}": "Не вдалося очистити кошик: {error}",
    "Export note": "Експортувати нотатку",
    "HTML files": "Файли HTML",
    "PDF files": "Файли PDF",
    "Exported: {path}": "Експортовано: {path}",
    "Could not export note: {error}": "Не вдалося експортувати нотатку: {error}",
    "Could not create backup: {error}": "Не вдалося створити резервну копію: {error}",
    "Backup created: {path}": "Резервну копію створено: {path}",
    "Backups": "Резервні копії",
    "No backups yet": "Резервних копій ще немає",
    "{count} backup · latest {stamp}": "{count} копія · остання {stamp}",
    "{count} backups · latest {stamp}": "{count} копій · остання {stamp}",
    "limit exceeded": "ліміт перевищено",
    "Automatic copies of all notes. Backups live outside the notes folder; starred archives can only be deleted manually.": "Автоматичні копії всіх нотаток. Вони лежать поза текою нотаток; архіви з зіркою можна видалити лише вручну.",
    "Create backups automatically": "Створювати резервні копії автоматично",
    "Every": "Інтервал",
    "minutes": "хвилин",
    "hours": "годин",
    "days": "днів",
    "weeks": "тижнів",
    "Backup folder": "Папка резервних копій",
    "Leave empty to use the default folder. The folder is never deleted by uninstall.": "Залиште порожнім, щоб використовувати типову папку. Деінсталятор ніколи її не видаляє.",
    "Warn when there are more than": "Попереджати, якщо копій більше ніж",
    "0 = no limit (default)": "0 = без обмежень (типово)",
    "backups": "резервних копій",
    "Automatically delete backups older than": "Автоматично видаляти копії, старші за",
    "months": "місяців",
    "years": "років",
    "Starred backups and legacy archives are always kept. This option never deletes files from other programs.": "Копії з зіркою та старі архіви завжди зберігаються. Ця опція ніколи не видаляє файли інших програм.",
    "Backups: {count} · {size} · starred: {starred}": "Копій: {count} · {size} · з зіркою: {starred}",
    "latest: {stamp}": "остання: {stamp}",
    "Backup limit exceeded: {count}/{limit}. Delete old backups manually or enable automatic cleanup.": "Перевищено ліміт резервних копій: {count}/{limit}. Видаліть старі копії вручну або увімкніть автоматичне очищення.",
    "Backup limit exceeded: {count}/{limit}. Delete old backups manually or enable automatic cleanup below.": "Перевищено ліміт резервних копій: {count}/{limit}. Видаліть старі копії вручну або увімкніть автоматичне очищення нижче.",
    "Removed old backups automatically: {count}": "Автоматично видалено старі резервні копії: {count}",
    "Backup folder updated": "Папку резервних копій оновлено",
    "Backup folder reset to default": "Папку резервних копій скинуто до типової",
    "Choose backup folder": "Виберіть папку резервних копій",
    "Could not open backup folder: {error}": "Не вдалося відкрити папку резервних копій: {error}",
    "Manage backups…": "Керувати резервними копіями…",
    "Manage backups": "Керування резервними копіями",
    "Star protects a backup from automatic cleanup. Deleting is always a manual action.": "Зірка захищає копію від автоматичного очищення. Видалення завжди виконується вручну.",
    "No backups yet.": "Резервних копій ще немає.",
    "Star": "Позначити зіркою",
    "Unstar": "Зняти зірку",
    " · starred": " · з зіркою",
    " · legacy": " · стара версія",
    "{stamp} · {size}{starred}{legacy}": "{stamp} · {size}{starred}{legacy}",
    "Delete backup": "Видалити резервну копію",
    "Delete backup?": "Видалити резервну копію?",
    "This permanently deletes the selected backup file.": "Це назавжди видалить вибраний файл резервної копії.",
    "Backup deleted": "Резервну копію видалено",
    "Backup starred": "Резервну копію позначено зіркою",
    "Backup unstarred": "Зірку з резервної копії знято",
    "Could not update backup: {error}": "Не вдалося оновити резервну копію: {error}",
    "Could not delete backup: {error}": "Не вдалося видалити резервну копію: {error}",
    "Backup now": "Створити резервну копію зараз",
    "Open backup folder": "Відкрити папку резервних копій",
    "Check integrity": "Перевірити цілісність",
    "No backups to check": "Немає резервних копій для перевірки",
    "All {count} backups are valid": "Усі резервні копії ({count}) цілі",
    "Backup check: {valid}/{total} valid. Damaged: {names}": "Перевірка: цілих {valid}/{total}. Пошкоджені: {names}",
    "Restore": "Відновити",
    "Open Notes first to restore a backup.": "Спочатку відкрийте Notes, щоб відновити резервну копію.",
    "Choose sync folder": "Виберіть папку для синхронізації",
    "Could not sync notes: {error}": "Не вдалося синхронізувати нотатки: {error}",
    "Notes synced to: {path}": "Нотатки синхронізовано в: {path}",
    # ------------------------------------------------------------------
    # Command palette, shortcuts, quick capture, plugins and AI
    # ------------------------------------------------------------------
    "Command palette": "Палітра команд",
    "Quick switcher": "Швидкий перехід",
    "Type a command, note or module — ↑/↓ to choose, Enter to run": (
        "Введіть команду, нотатку або модуль — ↑/↓ для вибору, Enter для"
        "запуску"
    ),
    "Esc closes · commands and actions are searchable": (
        "Esc закриває · команди й дії доступні для пошуку"
    ),
    "Notes and commands · Esc closes": "Нотатки й команди · Esc закриває",
    "Esc closes · every command is searchable": (
        "Esc закриває · кожна команда доступна для пошуку"
    ),
    "Nothing found. Try another word or press Esc.": (
        "Нічого не знайдено. Спробуйте інше слово або натисніть Esc."
    ),
    "Every action is editable. Use the search box, press Record, then press the new combination. Global shortcuts also work while Omnify is hidden in the system tray.": (
        "Кожну дію можна змінити. Скористайтеся пошуком, натисніть «Записати»"
        "й натисніть нову комбінацію. Глобальні скорочення працюють і тоді,"
        "коли Omnify сховано в системному лотку."
    ),
    "Search commands…": "Пошук команд…",
    "Restore every default shortcut": "Відновити всі типові скорочення",
    "Global": "Глобальне",
    "Works while Omnify is hidden in the tray": "Працює, коли Omnify сховано в лотку",
    "Record…": "Записати…",
    "Record": "Записати",
    "Reset this shortcut": "Скинути це скорочення",
    "Reset all": "Скинути все",
    "Not assigned": "Не призначено",
    "Record shortcut": "Записати скорочення",
    "Capture the next key combination": "Захопити наступну комбінацію клавіш",
    "Press Record and then the key combination, or type it below (for example alt+f4 or ctrl+shift+p).": (
        "Натисніть «Записати» й натисніть комбінацію або введіть її нижче"
        "(наприклад, alt+f4 чи ctrl+shift+p)."
    ),
    "Enter a key combination": "Введіть комбінацію клавіш",
    "Already used by: {names}": "Уже використовується: {names}",
    "Press keys now…": "Натисніть клавіші…",
    "Listening…": "Очікування…",
    "Shortcut cleared — press Save": "Скорочення очищено — натисніть «Зберегти»",
    "Quick capture": "Швидке захоплення",
    "Quick capture…": "Швидке захоплення…",
    "Capture a thought, a selection or research notes. The clipboard is pre-filled when available.": (
        "Захопіть думку, виділений текст або дослідницькі нотатки. Буфер"
        "обміну підставляється автоматично, якщо доступний."
    ),
    "Save to:": "Зберегти до:",
    "Paste clipboard": "Вставити з буфера",
    "Ctrl+Enter saves · Esc closes": "Ctrl+Enter зберігає · Esc закриває",
    "Capture": "Захопити",
    "Nothing to capture yet": "Ще нічого захоплювати",
    "Notes module is not available": "Модуль нотаток недоступний",
    "Could not create a note": "Не вдалося створити нотатку",
    "Captured to Notes": "Захоплено в нотатки",
    "Quick capture is not available yet": "Швидке захоплення ще недоступне",
    "Plugins": "Плагіни",
    "Drop a plugin folder into the Omnify plugins directory and press Rescan. External plugins run Python code with your permissions, so they stay disabled until you trust them.": (
        "Додайте теку плагіна до каталогу плагінів Omnify і натисніть"
        "«Пересканувати». Зовнішні плагіни виконують Python-код із вашими"
        "правами, тому вони вимкнені, доки ви їм не довірите."
    ),
    "Open plugins folder": "Відкрити теку плагінів",
    "Rescan": "Пересканувати",
    "Built-in plugins ship with Omnify; external folders are read from the data directory.": (
        "Вбудовані плагіни постачаються разом з Omnify; зовнішні теки"
        "читаються з каталогу даних."
    ),
    "No description": "Немає опису",
    "Permissions: {items}": "Дозволи: {items}",
    "Plugin errors:": "Помилки плагінів:",
    "Built-in": "Вбудований",
    "External": "Зовнішній",
    "Loaded": "Завантажено",
    "Enabled": "Увімкнено",
    "Disabled": "Вимкнено",
    "Reload": "Перезавантажити",
    "Disable": "Вимкнути",
    "Enable": "Увімкнути",
    "Trust {name}?": "Довіряти «{name}»?",
    "Plugins run Python code with your user permissions and can read files, use the network and change your data. Only enable plugins you trust.": (
        "Плагіни виконують Python-код із правами вашого користувача, можуть"
        "читати файли, використовувати мережу та змінювати дані. Увімкнюйте"
        "лише ті плагіни, яким довіряєте."
    ),
    "Trust and enable": "Довіряти й увімкнути",
    "AI Assistant": "AI-помічник",
    "Ask AI…": "Запитати AI…",
    "Ask AI": "Запитати AI",
    "Question": "Запитання",
    "AI answer": "Відповідь AI",
    "Ask AI about the current note…": "Запитати AI про поточну нотатку…",
    "Ask AI about this note": "Запитати AI про цю нотатку",
    "Question about “{title}”": "Запитання про «{title}»",
    "Summarize the current note with AI": "Підсумувати поточну нотатку через AI",
    "Summary · {title}": "Підсумок · {title}",
    "Create a calendar event with AI…": "Створити подію календаря через AI…",
    "Create a calendar event": "Створити подію календаря",
    "Describe the event, for example: “Dentist tomorrow at 15:00 for 45 minutes”. Today is {date}.": (
        "Опишіть подію, наприклад: «Стоматолог завтра о 15:00 на 45 хвилин»."
        "Сьогодні {date}."
    ),
    "Create “{title}”?": "Створити «{title}»?",
    "Create event": "Створити подію",
    "Event created: {title}": "Подію створено: {title}",
    "Could not create the event: {error}": "Не вдалося створити подію: {error}",
    "Could not understand the event: {error}": "Не вдалося зрозуміти подію: {error}",
    "all day": "весь день",
    "Research with AI and the browser…": "Дослідження з AI і браузером…",
    "Research with AI": "Дослідження з AI",
    "What should the browser search for?": "Що шукати в браузері?",
    "Research notes": "Дослідницькі нотатки",
    "Research · {topic}": "Дослідження · {topic}",
    "AI Assistant settings…": "Налаштування AI-помічника…",
    "Open Settings → AI Assistant to configure the model.": (
        "Відкрийте «Налаштування → AI-помічник», щоб налаштувати модель."
    ),
    "AI is thinking…": "AI думає…",
    "AI is already working on a request.": "AI вже виконує запит.",
    "Open a note first.": "Спочатку відкрийте нотатку.",
    "The current note is empty.": "Поточна нотатка порожня.",
    "This provider is not local. The text you send, including any note content you choose to include, leaves this computer and is handled under that provider's privacy policy. Prefer a local model for private notes.": (
        "Цей провайдер не локальний. Надісланий текст, зокрема вміст нотатки,"
        "залишає цей комп'ютер і обробляється за політикою конфіденційності"
        "провайдера. Для приватних нотаток обирайте локальну модель."
    ),
    "Send this prompt to {provider}?": "Надіслати запит до {provider}?",
    "Send to cloud provider": "Надіслати хмарному провайдеру",
    "Cloud AI requires an explicit privacy acknowledgement in AI settings.": (
        "Хмарний AI потребує явного підтвердження конфіденційності в"
        "налаштуваннях AI."
    ),
    "Local providers keep prompts on this computer. Cloud providers receive the prompt and any note text you include; they ask for confirmation once before the first request.": (
        "Локальні провайдери залишають запити на цьому комп'ютері. Хмарні"
        "отримують запит і текст нотатки, який ви додали; перед першим"
        "запитом вони один раз просять підтвердження."
    ),
    "Provider": "Провайдер",
    "Base URL": "Базова URL-адреса",
    "Model": "Модель",
    "API key": "API-ключ",
    "optional for local models": "не потрібен для локальних моделей",
    "The key is stored in Omnify's local settings. For stronger separation, leave it empty and set OMNIFY_AI_API_KEY.": (
        "Ключ зберігається в локальних налаштуваннях Omnify. Для кращого"
        "розділення залиште поле порожнім і задайте OMNIFY_AI_API_KEY."
    ),
    "Advanced": "Додатково",
    "Temperature": "Температура",
    "Timeout (seconds)": "Тайм-аут (секунди)",
    "Local endpoint: prompts stay on this computer.": (
        "Локальна адреса: запити залишаються на цьому комп'ютері."
    ),
    "⚠ Cloud provider: prompts and any included note text leave this computer. Never send secrets you are not allowed to share.": (
        "⚠ Хмарний провайдер: запити й доданий текст нотаток залишають цей"
        "комп'ютер. Не надсилайте секрети, які не можна розкривати."
    ),
    "Load defaults": "Завантажити типові",
    "Test connection": "Перевірити з'єднання",
    "Save AI settings": "Зберегти налаштування AI",
    "AI settings saved": "Налаштування AI збережено",
    "Saved. The model is ready to use.": "Збережено. Модель готова до роботи.",
    "Testing the connection…": "Перевірка з'єднання…",
    "Connection OK: {answer}": "З'єднання працює: {answer}",
    "Copied to the clipboard": "Скопійовано в буфер обміну",
    "Save as note": "Зберегти як нотатку",
    "Saved to Notes": "Збережено в нотатках",
    "The Notes module is not available": "Модуль нотаток недоступний",
    "The Calendar module is not available": "Модуль календаря недоступний",
    "Continue": "Продовжити",
    "Copy": "Копіювати",
    "Plugins and AI settings…": "Плагіни й налаштування AI…",
    "Clear calculator expression": "Очистити вираз калькулятора",
    "Evaluate calculator expression": "Обчислити вираз калькулятора",
    "Copy calculator result": "Скопіювати результат калькулятора",
    "Focus calculator expression": "Перейти до виразу калькулятора",
    "Open the private browser": "Відкрити приватний браузер",
    "Open a private browser window": "Відкрити приватне вікно браузера",
    "Open Tor Browser (maximum privacy)": (
        "Відкрити Tor Browser (максимальна приватність)"
    ),
    "Focus the browser search box": "Перейти до пошуку в браузері",
    "New calendar event": "Нова подія календаря",
    "Search calendar": "Пошук у календарі",
    "Previous day/week": "Попередній день/тиждень",
    "Next day/week": "Наступний день/тиждень",
    "Undo calendar change": "Скасувати зміну календаря",
    "Redo calendar change": "Повторити зміну календаря",
    "Zoom calendar in": "Збільшити календар",
    "Zoom calendar out": "Зменшити календар",
    "Reset calendar zoom": "Скинути масштаб календаря",
    "Calendar: Week page": "Календар: тиждень",
    "Calendar: Day page": "Календар: день",
    "Calendar: Habits page": "Календар: звички",
    "Calendar: Statistics page": "Календар: статистика",
    "Calendar: Goals page": "Календар: цілі",
    "Calendar: Search page": "Календар: пошук",
    "Start or resume the first timer": "Запустити або продовжити перший таймер",
    "Toggle the first timer": "Перемкнути перший таймер",
    "Pause all running timers": "Призупинити всі таймери",
    "Resume all paused timers": "Продовжити всі таймери",
    "Reset all timers": "Скинути всі таймери",
    "Add a timer": "Додати таймер",
    "Start or pause the stopwatch": "Запустити або призупинити секундомір",
    "Start/resume the stopwatch": "Запустити/продовжити секундомір",
    "Pause the stopwatch": "Призупинити секундомір",
    "Record a stopwatch lap": "Записати коло секундоміра",
    "Reset the stopwatch": "Скинути секундомір",
    "New alarm…": "Новий будильник…",
    "Stop the ringing alarm": "Вимкнути будильник, що дзвонить",
    "Snooze the ringing alarm": "Відкласти будильник",
    "Refresh Task Manager": "Оновити менеджер задач",
    "Search running processes": "Пошук запущених процесів",
    "Task Manager: Processes tab": "Менеджер задач: процеси",
    "Task Manager: Performance tab": "Менеджер задач: продуктивність",
    "Task Manager: Services tab": "Менеджер задач: служби",
    "Task Manager: Startup tab": "Менеджер задач: автозапуск",
    "Task Manager: Users tab": "Менеджер задач: користувачі",
    "Task Manager: Details tab": "Менеджер задач: деталі",
    "New note from template…": "Нова нотатка з шаблону…",
    "Manage note templates…": "Керувати шаблонами нотаток…",
    "Save note now": "Зберегти нотатку зараз",
    "Bold selection": "Жирний текст",
    "Italic selection": "Курсив",
    "Insert Markdown link": "Вставити Markdown-посилання",
    "Open version history…": "Відкрити історію версій…",
    "Open Trash…": "Відкрити кошик…",
    "Back up notes now": "Створити резервну копію нотаток",
    "Export note as HTML…": "Експортувати нотатку в HTML…",
    "Export note as PDF…": "Експортувати нотатку в PDF…",
    "Export note as Markdown…": "Експортувати нотатку в Markdown…",
    "Application": "Застосунок",
    "Navigation": "Навігація",
    # Calendar quick add and meeting notes
    "Quick add from a phrase": "Швидке додавання з фрази",
    "Quick add: Lunch tomorrow at 13:00": (
        "Швидке додавання: Обід завтра о 13:00"
    ),
    "Understands today/tomorrow, weekday names, 2026-11-20, 20.11.2026, month names, 13:00-14:30, 9am and durations such as “for 30 minutes”. English and Ukrainian are both supported.": (
        "Розуміє «сьогодні»/«завтра», назви днів тижня, 2026-11-20, "
        "20.11.2026, назви місяців, 13:00-14:30, 9am і тривалість на "
        "кшталт «протягом 30 хвилин». Працює англійською та українською."
    ),
    "Add the event from the quick-add line": (
        "Додати подію з рядка швидкого додавання"
    ),
    "Room, address or video link (optional)": (
        "Кімната, адреса або посилання на відеозустріч (необов'язково)"
    ),
    "Names separated by commas (optional)": (
        "Імена через кому (необов'язково)"
    ),
    "Create a Markdown note with the agenda, attendees and action items": (
        "Створити Markdown-нотатку з порядком денним, учасниками та "
        "завданнями"
    ),
    "Meeting note": "Нотатка зустрічі",
    "The Notes module is not available right now.": (
        "Модуль нотаток зараз недоступний."
    ),
    "Could not create the meeting note.": (
        "Не вдалося створити нотатку зустрічі."
    ),
    "Meeting note created: {title}": (
        "Нотатку зустрічі створено: {title}"
    ),
    "Added: {title} · {date} · {time}": (
        "Додано: {title} · {date} · {time}"
    ),
    "Type an event, for example: Lunch tomorrow at 13:00": (
        "Введіть подію, наприклад: Обід завтра о 13:00"
    ),
    "Invalid date: {value}": "Неправильна дата: {value}",
    "Invalid time: {value}": "Неправильний час: {value}",
    # Browser bangs and reading list
    "Search bangs (type !name before the query):": (
        "Пошукові скорочення (введіть !назва перед запитом):"
    ),
    "Tip: start with a bang, for example !w Wikipedia, !yt YouTube or !gh Python.": (
        "Порада: почніть зі скорочення, наприклад !w Wikipedia, "
        "!yt YouTube або !gh Python."
    ),
    "Reading list": "Список для читання",
    "Open all": "Відкрити все",
    "Open up to 10 saved pages in new tabs": (
        "Відкрити до 10 збережених сторінок у нових вкладках"
    ),
    "Paste a URL to save for later…": (
        "Вставте URL, щоб зберегти на потім…"
    ),
    "{count} saved": "Збережено: {count}",
    "Reading list is empty.": "Список для читання порожній.",
    "The list is stored locally in Omnify; no account or sync is involved.": (
        "Список зберігається локально в Omnify; обліковий запис і "
        "синхронізація не потрібні."
    ),
    "Remove": "Видалити",
    "Saved: {title}": "Збережено: {title}",
    "Opened {count} saved pages": "Відкрито збережених сторінок: {count}",
    "Open a page first, then save it for later.": (
        "Спочатку відкрийте сторінку, потім збережіть її на потім."
    ),
    "Focus the browser reading list": "Перейти до списку для читання",
    "Save the last opened page for later": (
        "Зберегти останню відкриту сторінку на потім"
    ),
    "A reading-list entry needs a URL": (
        "Запис у списку для читання потребує URL"
    ),
    "Only web addresses can be saved for later": (
        "На потім можна зберігати лише веб-адреси"
    ),
    "Unknown search shortcut": "Невідоме пошукове скорочення",
    "Unknown search shortcut !{name}": (
        "Невідоме пошукове скорочення !{name}"
    ),
    "Add a query after !{name}": "Додайте запит після !{name}",
    "Unsafe URL scheme": "Небезпечна схема URL",
    "Browser selection actions": "Дії з виділенням у браузері",
    "Add “Move selection to a new note” and “Solve in Calculator” to the page context menu.": (
        "Додає «Перемістити виділення в нову нотатку» і "
        "«Розв'язати в Калькуляторі» до контекстного меню сторінки."
    ),
    "Reinstall selection actions": "Перевстановити дії з виділенням",
    "Select text on any page and use “Move selection to a new note” or “Solve in Calculator” from the context menu. Omnify listens on 127.0.0.1 only; selected text is never sent anywhere else.": (
        "Виділіть текст на будь-якій сторінці й виберіть у контекстному меню "
        "«Перемістити виділення в нову нотатку» або «Розв'язати в "
        "Калькуляторі». Omnify слухає лише 127.0.0.1; виділений текст "
        "нікуди більше не надсилається."
    ),
    "Selection actions ready on 127.0.0.1:{port} — restart the browser after reinstalling.": (
        "Дії з виділенням готові на 127.0.0.1:{port} — перезапустіть браузер "
        "після перевстановлення."
    ),
    "Selection actions installed — Omnify must stay running to receive them.": (
        "Дії з виділенням встановлено — Omnify має бути запущений, щоб їх "
        "приймати."
    ),
    "Selection actions are not installed — press “Reinstall selection actions”.": (
        "Дії з виділенням не встановлено — натисніть «Перевстановити дії з "
        "виділенням»."
    ),
    "Could not install selection actions: {error}": (
        "Не вдалося встановити дії з виділенням: {error}"
    ),
    "Selection actions installed — restart the browser to apply": (
        "Дії з виділенням встановлено — перезапустіть браузер, щоб застосувати"
    ),
    "Notes is not available": "Нотатки недоступні",
    "Selection saved to a new note": "Виділення збережено в нову нотатку",
    "Could not save the selection to Notes": (
        "Не вдалося зберегти виділення в Нотатки"
    ),
    "Could not solve the selected text": "Не вдалося розв'язати виділений текст",
    "Could not handle the browser selection": (
        "Не вдалося обробити виділення з браузера"
    ),

    # Detachable module windows
    "Open the current module in a separate window": (
        "Відкрити поточний модуль в окремому вікні"
    ),
    "Open current module in a separate window": (
        "Відкрити поточний модуль в окремому вікні"
    ),
    "Move the active module into its own window.": (
        "Перемістити активний модуль у власне вікно."
    ),
    "Move this module into its own window; closing it returns the module "
    "to the main window.": (
        "Перемістити цей модуль у власне вікно; закриття повертає модуль "
        "в головне вікно."
    ),
    "Open Home in a separate window": "Відкрити Головну в окремому вікні",
    "Open Calculator in a separate window": (
        "Відкрити Калькулятор в окремому вікні"
    ),
    "Open Browser in a separate window": "Відкрити Браузер в окремому вікні",
    "Open Calendar in a separate window": "Відкрити Календар в окремому вікні",
    "Open Timer in a separate window": "Відкрити Таймер в окремому вікні",
    "Open Stopwatch in a separate window": (
        "Відкрити Секундомір в окремому вікні"
    ),
    "Open Alarm clock in a separate window": (
        "Відкрити Будильник в окремому вікні"
    ),
    "Open Task Manager in a separate window": (
        "Відкрити Диспетчер задач в окремому вікні"
    ),
    "Open Notes in a separate window": "Відкрити Нотатки в окремому вікні",
    "This module is open in a separate window.": (
        "Цей модуль відкрито в окремому вікні."
    ),
    "Changes stay live because both windows share one Omnify process. Closing "
    "that window returns the module here.": (
        "Зміни залишаються живими, бо обидва вікна належать одному процесу "
        "Omnify. Закриття того вікна повертає модуль сюди."
    ),
    "Focus window": "Показати вікно",
    "Return to main window": "Повернути в головне вікно",
    "Show the main Omnify window and keep this one open": (
        "Показати головне вікно Omnify і залишити це вікно відкритим"
    ),
    "Move this module back into the main Omnify window": (
        "Повернути цей модуль у головне вікно Omnify"
    ),
    "Nothing to detach": "Немає що відокремити",
}
