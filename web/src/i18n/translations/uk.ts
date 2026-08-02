import type { Translations } from './en'
import { pluralize } from '../plural'

export const uk: Translations = {
  app: {
    title: 'AI Вокальний Тренер',
    logout: 'Вийти',
    languageLabel: 'Мова',
    sectionLabel: 'Розділ',
    navAnalyze: 'Аналіз',
    navProgress: 'Прогрес',
    analyzeAnotherSong: 'Проаналізувати іншу пісню',
    submitting: 'Надсилання…',
    skipToContent: 'Перейти до вмісту',
    loading: 'Завантаження…',
  },
  errorAlert: {
    queueFull: 'Черга аналізів зараз заповнена. Спробуйте, будь ласка, за кілька хвилин.',
    analysisRateLimited: 'Ви досягли годинного ліміту аналізів. Спробуйте пізніше.',
    analysisNotQueued: 'Цей аналіз більше не можна скасувати.',
    analysisNotFailed: 'Повторити можна лише невдалий аналіз.',
    youtubeImportDisabled: 'Імпорт з YouTube наразі вимкнено.',
    invalidYoutubeUrl: 'Це не схоже на дійсне посилання YouTube.',
    youtubeVideoTooLong: 'Це відео довше за дозволений ліміт.',
    unsupportedAudioFormat:
      'Непідтримуваний формат аудіо. Використовуйте mp3, wav, m4a, flac або ogg.',
    audioTooLarge: 'Цей файл більший за ліміт завантаження.',
    audioTooLong: 'Цей запис довший за дозволений ліміт.',
    generic: 'Щось пішло не так. Спробуйте, будь ласка, ще раз.',
    retryAfter: (seconds: number) => ` Спробуйте ще раз через ${seconds}с.`,
  },
  login: {
    heading: 'Увійти',
    email: 'Електронна пошта',
    password: 'Пароль',
    submitPending: 'Вхід…',
    submit: 'Увійти',
    needAccount: 'Немає акаунту? Зареєструватися',
  },
  register: {
    heading: 'Створити акаунт',
    displayName: "Ім'я",
    email: 'Електронна пошта',
    password: 'Пароль',
    passwordHint: 'Щонайменше 10 символів. Уникайте поширених паролів.',
    submitPending: 'Створення акаунту…',
    submit: 'Створити акаунт',
    haveAccount: 'Вже маєте акаунт? Увійти',
  },
  verify: {
    heading: 'Перевірте пошту',
    sentCode: (email: string) => `Ми надіслали 6-значний код на ${email}. Він дійсний 24 години.`,
    codeLabel: 'Код підтвердження',
    submitPending: 'Перевірка…',
    submit: 'Підтвердити пошту',
    resendPending: 'Надсилання…',
    resend: 'Надіслати код повторно',
    resent: 'Якщо такий акаунт існує, новий код надіслано.',
  },
  addSong: {
    heading: 'Додати пісню',
    sourceLabel: 'Джерело пісні',
    sourceUpload: 'Завантажити файл',
    sourceYoutube: 'Посилання YouTube',
    title: 'Назва',
    artist: "Виконавець (необов'язково)",
    audioFile: 'Аудіофайл',
    audioFileHint: 'mp3, wav, m4a, flac або ogg. До 15 МБ / 6 хвилин.',
    youtubeDisclaimer:
      'Лише для особистого некомерційного використання. Завантаження аудіо з YouTube може ' +
      'суперечити його Умовам використання та правам власника пісні.',
    youtubeUrl: 'URL YouTube',
    youtubeUrlPlaceholder: 'https://www.youtube.com/watch?v=...',
    titleOverride: "Змінити назву (необов'язково)",
    submitPending: 'Додавання пісні…',
    submit: 'Додати пісню',
    hintUpload: 'Введіть назву та оберіть аудіофайл, щоб продовжити.',
    hintYoutube: 'Введіть URL YouTube, щоб продовжити.',
  },
  recordingCapture: {
    heading: 'Запишіть свою спробу',
    modeLabel: 'Режим аналізу',
    modeClean: 'А капела',
    modeMixed: 'З музикою',
    cleanTitle: 'Рекомендовано: співайте а капела',
    cleanBody:
      'Без інструментів, без бек-треку, без музики в кімнаті — лише ваш голос, у навушниках. ' +
      'Це вимірює всі 6 аспектів (висота тону, ритм, дихання, динаміку, вібрато й тембр) із ' +
      'найвищою точністю.',
    mixedTitle: 'Спів під музику',
    mixedBody:
      'Записуєте себе під гітару, піаніно, гурт чи бек-трек? Оберіть це. Точно вимірюються лише ' +
      'висота тону й ритм; динаміка та вібрато теж оцінюються, але менш точно, а дихання й тембр ' +
      'взагалі не можна виміряти за наявності стороннього звуку — у звіті ці два аспекти буде ' +
      'позначено як невиміряні, а не як поганий результат.',
    sourceLabel: 'Джерело запису',
    sourceRecord: 'Записати в браузері',
    sourceUpload: 'Завантажити файл',
    stateIdle: 'Готово до запису.',
    stateRequesting: 'Запит доступу до мікрофона…',
    stateRecording: 'Йде запис…',
    stateRecorded: 'Запис завершено. Прослухайте нижче або перезапишіть.',
    reRecord: 'Перезаписати',
    startRecording: 'Почати запис',
    stop: 'Зупинити',
    fileLabel: 'Файл запису',
    fileHint: 'mp3, wav, m4a, flac або ogg. До 6 хвилин.',
    useThisRecording: 'Використати цей запис',
  },
  mediaRecorder: {
    micError: 'Не вдалося отримати доступ до мікрофона.',
  },
  queueStatus: {
    heading: 'Статус аналізу',
    loadingStatus: 'Завантаження статусу…',
    statusWaitingForReference: 'Очікування готовності пісні',
    statusQueued: 'У черзі',
    statusProcessing: 'Обробка',
    statusDone: 'Готово',
    statusFailed: 'Помилка',
    statusCanceled: 'Скасовано',
    numberInQueue: (position: number) => `Ви номер ${position} у черзі.`,
    waitingForReferenceBody:
      'Ця пісня ще готується. Ваш аналіз почнеться автоматично, щойно вона буде готова.',
    waiting: (duration: string) =>
      `Очікування: ${duration} — ця сторінка оновлюється сама, перезавантажувати не потрібно.`,
    stageStatus: (
      stage: string,
      index: number | undefined,
      total: number | undefined,
      elapsed: string | undefined,
    ) => {
      let s = 'Етап'
      if (index !== undefined && total !== undefined) s += ` ${index} з ${total}`
      s += `: ${stage}`
      if (elapsed !== undefined) s += ` — виконується ${elapsed}`
      return s
    },
    stageDuration: (name: string, duration: string) => `${name} — ${duration}`,
    cancelPending: 'Скасування…',
    cancel: 'Скасувати',
    retryPending: 'Повторення…',
    retry: 'Повторити',
  },
  analysisReport: {
    aspectPitch: 'Висота тону',
    aspectRhythm: 'Ритм',
    aspectBreath: 'Дихання',
    aspectDynamics: 'Динаміка',
    aspectVibrato: 'Вібрато',
    aspectTimbre: 'Тембр',
    overall: 'Загалом',
    modeClean: 'а капела',
    modeMixed: 'з музикою',
    unavailableAccompaniment: 'у записі був присутній сторонній звук',
    notMeasured: 'Не виміряно',
    notMeasuredTitle: (reason: string) => `Не виміряно: ${reason}`,
    warningAccompanimentInCleanMode:
      'Це проаналізовано як а капела, але ми виявили звук, що не схожий на сольний голос, тож ' +
      'результати можуть бути менш точними, ніж зазвичай. Якщо ви співали під музику, повторіть ' +
      'цей аналіз у режимі «з музикою».',
    warningModeDowngradedToClean:
      'Супроводу не виявлено, тому аналіз пройшов у режимі а капела замість «з музикою» — це ' +
      'дає точніший результат, а не є помилкою.',
    warningLittleVoiceDetected:
      'У цьому записі виявлено дуже мало голосу, що знижує довіру до результатів.',
    warningWeakAlignment:
      'Ваш запис погано збігся з референсним треком, що знижує довіру до результатів.',
    warningKeyShiftOutOfRange:
      'Виявлено зміщення тональності, але воно було занадто великим для впевненої корекції.',
    warningLengthMismatchPartialAnalysis:
      'Ваш запис і референсна пісня суттєво відрізнялись за тривалістю, тож проаналізовано лише ' +
      'спільну частину — ці результати відображають частину пісні, а не всю пісню.',
    warningReferenceStartOffsetDetected:
      'Референсна пісня починається з частини, якої немає у вашому записі (наприклад, ' +
      'інструментальний програш), тож ми врахували, з якого місця насправді починається ваш запис.',
    confidenceHigh: 'Умови запису: добрі',
    confidenceMedium: 'Умови запису: середні',
    confidenceLow: 'Умови запису: обмежені',
    confidenceExplanationHigh: 'Ці результати надійні.',
    confidenceExplanationMedium:
      'Ці результати придатні для використання, але менш точні, ніж дав би чистий сольний запис.',
    confidenceExplanationLow:
      'Ці результати приблизні — сприймайте їх як загальний напрямок, а не точний вимір.',
    youSelectedPrefix: 'Ви обрали ',
    youSelectedMiddle: ', але цей аналіз фактично пройшов як ',
    youSelectedSuffix: ' — виходячи з того, що ми почули в записі.',
    keyShift: (semitones: number, direction: 'above' | 'below') =>
      `Ваш запис був на ${semitones} ${pluralize('uk', semitones, { one: 'півтон', few: 'півтони', many: 'півтонів', other: 'півтона' })} ` +
      `${direction === 'above' ? 'вище' : 'нижче'} за тональність референсу; результати вище вже це враховують.`,
  },
  analysisResult: {
    yourRecording: 'Ваш запис',
  },
  pianoRoll: {
    caption: 'Висота тону в часі: ваш голос порівняно з референсною мелодією.',
    legendYou: 'Ваш голос',
    legendReference: 'Референс',
    legendOffPitch: 'Фальшива нота',
    summary: (offPitchCount: number) =>
      'Піано-рол: ваша крива висоти тону поверх референсної кривої, ' +
      `${offPitchCount} ${pluralize('uk', offPitchCount, { one: 'фальшива нота', few: 'фальшиві ноти', many: 'фальшивих нот', other: 'фальшивої ноти' })} позначено червоним`,
  },
  progressChart: {
    noSessions: 'Ще немає сесій.',
    summary: (
      sessionCount: number,
      firstScore: number,
      firstDate: string,
      lastScore: number,
      lastDate: string,
      mixedModes: boolean,
    ) =>
      `Лінійний графік вашого загального результату за ${sessionCount} ` +
      `${pluralize('uk', sessionCount, { one: 'сесію', few: 'сесії', many: 'сесій', other: 'сесії' })}, ` +
      `від ${firstScore} (${firstDate}) до ${lastScore} (${lastDate}).` +
      (mixedModes
        ? ' Включає сесії а капела та з музикою, позначені окремо — їхні результати не можна напряму порівнювати.'
        : ''),
    legendClean: 'А капела',
    legendMixed: 'З музикою',
  },
  progressPage: {
    heading: 'Ваш прогрес',
    loading: 'Завантаження прогресу…',
    empty: 'Завершіть свій перший аналіз, щоб почати відстежувати прогрес.',
    noChange: 'Без змін',
    up: (amount: number) => `+${amount}`,
    down: (amount: number) => `-${amount}`,
    latest: 'Останній',
    best: 'Найкращий',
    average: 'Середній',
    vsFirstSession: 'Порівняно з першою сесією',
    mixedModesNote:
      'Ця історія поєднує сесії а капела та з музикою (позначені нижче). Вони оцінюються за ' +
      'різними аспектами, тож результати не можна напряму порівнювати між режимами — порівнюйте ' +
      'лише сесії одного режиму між собою.',
    tableCaption: 'Ваші аналізи, від найновішого',
    columnDate: 'Дата',
    columnMode: 'Режим',
    columnOverallScore: 'Загальний результат',
    columnAction: 'Дія',
    viewAnalysis: 'Переглянути',
    modeClean: 'А капела',
    modeMixed: 'З музикою',
    backToHistory: 'Назад до історії',
    loadingSession: 'Завантаження аналізу…',
  },
  analysisError: {
    timeout:
      'Аналіз тривав надто довго і був зупинений. Спробуйте ще раз — коротший запис зазвичай допомагає.',
    internal: 'Під час аналізу цього запису щось пішло не так. Спробуйте, будь ласка, ще раз.',
    referenceTooQuiet:
      'Референсне аудіо цієї пісні не вдалося надійно обробити. Спробуйте іншу пісню.',
    noVoiceDetected:
      'Ми не змогли виявити спів у цьому записі. Переконайтесь, що мікрофон чітко записав ваш ' +
      'голос, і спробуйте ще раз.',
    melodyExtractionFailed:
      'Ми не змогли надійно відстежити мелодію вашого голосу в цьому записі. Спробуйте записати ' +
      'ще раз чіткіше.',
    alignmentFailed:
      'Цей запис недостатньо збігається з референсною піснею для аналізу. Переконайтесь, що ви ' +
      'записуєте ту саму пісню, і спробуйте ще раз.',
    alignmentTooLarge:
      'Цей запис занадто довгий для порівняння з референсом. Спробуйте коротший запис.',
    fallback: (code: string) => `Щось пішло не так (код: ${code}).`,
  },
}
