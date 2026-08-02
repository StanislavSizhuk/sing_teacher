import { pluralize } from '../plural'

/** Canonical dictionary: every other language's file is typed against
 * `Translations` (this shape), so a missing or mistyped key in another
 * language is a compile error, not a silent fallback to English at
 * runtime. */
export const en = {
  app: {
    title: 'AI Vocal Coach',
    logout: 'Log out',
    languageLabel: 'Language',
    sectionLabel: 'Section',
    navAnalyze: 'Analyze',
    navProgress: 'Progress',
    analyzeAnotherSong: 'Analyze another song',
    submitting: 'Submitting…',
    skipToContent: 'Skip to content',
    loading: 'Loading…',
  },
  errorAlert: {
    queueFull: 'The analysis queue is full right now. Please try again in a few minutes.',
    analysisRateLimited: "You've reached the hourly analysis limit. Try again later.",
    analysisNotQueued: 'This analysis can no longer be canceled.',
    analysisNotFailed: 'Only a failed analysis can be retried.',
    youtubeImportDisabled: 'YouTube import is currently disabled.',
    invalidYoutubeUrl: 'That does not look like a valid YouTube link.',
    youtubeVideoTooLong: 'That video is longer than the allowed limit.',
    unsupportedAudioFormat: 'Unsupported audio format. Use mp3, wav, m4a, flac or ogg.',
    audioTooLarge: 'That file is larger than the upload limit.',
    audioTooLong: 'That recording is longer than the allowed limit.',
    generic: 'Something went wrong. Please try again.',
    retryAfter: (seconds: number) => ` Try again in ${seconds}s.`,
  },
  login: {
    heading: 'Log in',
    email: 'Email',
    password: 'Password',
    submitPending: 'Logging in…',
    submit: 'Log in',
    needAccount: 'Need an account? Register',
  },
  register: {
    heading: 'Create an account',
    displayName: 'Display name',
    email: 'Email',
    password: 'Password',
    passwordHint: 'At least 10 characters. Avoid common passwords.',
    submitPending: 'Creating account…',
    submit: 'Create account',
    haveAccount: 'Already have an account? Log in',
  },
  verify: {
    heading: 'Check your email',
    sentCode: (email: string) => `We sent a 6-digit code to ${email}. It expires in 24 hours.`,
    codeLabel: 'Verification code',
    submitPending: 'Verifying…',
    submit: 'Verify email',
    resendPending: 'Sending…',
    resend: 'Resend code',
    resent: 'A new code was sent, if the account exists.',
  },
  addSong: {
    heading: 'Add a song',
    sourceLabel: 'Song source',
    sourceUpload: 'Upload file',
    sourceYoutube: 'YouTube link',
    title: 'Title',
    artist: 'Artist (optional)',
    audioFile: 'Audio file',
    audioFileHint: 'mp3, wav, m4a, flac or ogg. Up to 15 MB / 6 minutes.',
    youtubeDisclaimer:
      "For personal, non-commercial use only. Downloading audio from YouTube may conflict with its Terms of Service and the rights of the song's owner.",
    youtubeUrl: 'YouTube URL',
    youtubeUrlPlaceholder: 'https://www.youtube.com/watch?v=...',
    titleOverride: 'Title override (optional)',
    submitPending: 'Adding song…',
    submit: 'Add song',
    hintUpload: 'Enter a title and choose an audio file to continue.',
    hintYoutube: 'Enter a YouTube URL to continue.',
  },
  recordingCapture: {
    heading: 'Record your take',
    modeLabel: 'Analysis mode',
    modeClean: 'A cappella',
    modeMixed: 'With music',
    cleanTitle: 'Recommended: sing a cappella',
    cleanBody:
      'No instruments, no backing track, no music in the room -- just your voice, in headphones. ' +
      'This measures all 6 aspects (pitch, rhythm, breath, dynamics, vibrato and tone) at the ' +
      'highest accuracy.',
    mixedTitle: 'Singing with music',
    mixedBody:
      'Recording yourself with a guitar, piano, a band, or a backing track? Choose this. It only ' +
      'measures pitch and rhythm accurately; dynamics and vibrato are scored too but less ' +
      "precisely, and breath and tone can't be measured at all when other sound is present -- " +
      'the report will show those two as not measured, not as a bad score.',
    sourceLabel: 'Recording source',
    sourceRecord: 'Record in browser',
    sourceUpload: 'Upload a file',
    stateIdle: 'Ready to record.',
    stateRequesting: 'Requesting microphone access…',
    stateRecording: 'Recording…',
    stateRecorded: 'Recording finished. Listen back below, or re-record.',
    reRecord: 'Re-record',
    startRecording: 'Start recording',
    stop: 'Stop',
    fileLabel: 'Recording file',
    fileHint: 'mp3, wav, m4a, flac or ogg. Up to 6 minutes.',
    useThisRecording: 'Use this recording',
  },
  mediaRecorder: {
    micError: 'Could not access the microphone.',
  },
  queueStatus: {
    heading: 'Analysis status',
    loadingStatus: 'Loading status…',
    statusWaitingForReference: 'Waiting for song to be ready',
    statusQueued: 'Queued',
    statusProcessing: 'Processing',
    statusDone: 'Done',
    statusFailed: 'Failed',
    statusCanceled: 'Canceled',
    numberInQueue: (position: number) => `You are number ${position} in the queue.`,
    waitingForReferenceBody:
      "This song is still being prepared. Your analysis will start automatically once it's ready.",
    waiting: (duration: string) =>
      `Waiting ${duration} -- this page updates itself, no need to reload.`,
    stageStatus: (
      stage: string,
      index: number | undefined,
      total: number | undefined,
      elapsed: string | undefined,
    ) => {
      let s = 'Stage'
      if (index !== undefined && total !== undefined) s += ` ${index} of ${total}`
      s += `: ${stage}`
      if (elapsed !== undefined) s += ` — running ${elapsed}`
      return s
    },
    stageDuration: (name: string, duration: string) => `${name} — ${duration}`,
    cancelPending: 'Canceling…',
    cancel: 'Cancel',
    retryPending: 'Retrying…',
    retry: 'Retry',
  },
  analysisReport: {
    aspectPitch: 'Pitch',
    aspectRhythm: 'Rhythm',
    aspectBreath: 'Breath',
    aspectDynamics: 'Dynamics',
    aspectVibrato: 'Vibrato',
    aspectTimbre: 'Timbre',
    overall: 'Overall',
    modeClean: 'a cappella',
    modeMixed: 'with music',
    unavailableAccompaniment: 'other sound was present in the recording',
    notMeasured: 'Not measured',
    notMeasuredTitle: (reason: string) => `Not measured: ${reason}`,
    warningAccompanimentInCleanMode:
      "This was analyzed as a cappella, but we detected sound that doesn't look like a solo " +
      'voice, so scores may be less precise than usual. If you were singing with music, retry ' +
      'this analysis in "with music" mode.',
    warningModeDowngradedToClean:
      'No accompaniment was detected, so this ran in a cappella mode instead of "with music" -- ' +
      'that gives a more accurate result, not an error.',
    warningLittleVoiceDetected:
      'Very little of this recording contained a detectable voice, which lowers confidence in the scores.',
    warningWeakAlignment:
      "Your recording didn't line up well against the reference track, which lowers confidence in the scores.",
    warningKeyShiftOutOfRange:
      'A key shift was detected but was too large to confidently correct for.',
    warningLengthMismatchPartialAnalysis:
      'Your recording and the reference song were significantly different lengths, so only the ' +
      'matching portion was analyzed -- these scores reflect part of the song, not all of it.',
    confidenceHigh: 'Recording conditions: good',
    confidenceMedium: 'Recording conditions: medium',
    confidenceLow: 'Recording conditions: limited',
    confidenceExplanationHigh: 'These scores are reliable.',
    confidenceExplanationMedium:
      'These scores are usable but less precise than a clean, solo recording would give.',
    confidenceExplanationLow:
      'These scores are rough -- treat them as a general direction, not a precise measurement.',
    youSelectedPrefix: 'You selected ',
    youSelectedMiddle: ', but this analysis actually ran as ',
    youSelectedSuffix: ' based on what we heard in the recording.',
    keyShift: (semitones: number, direction: 'above' | 'below') =>
      `Your recording was ${semitones} ${pluralize('en', semitones, { one: 'semitone', other: 'semitones' })} ` +
      `${direction} the reference's key; scores above already account for it.`,
  },
  analysisResult: {
    yourRecording: 'Your recording',
  },
  pianoRoll: {
    caption: 'Pitch over time: your voice compared to the reference melody.',
    legendYou: 'Your voice',
    legendReference: 'Reference',
    legendOffPitch: 'Off-pitch note',
    summary: (offPitchCount: number) =>
      'Piano roll: your pitch curve over the reference pitch curve, ' +
      `${offPitchCount} ${pluralize('en', offPitchCount, { one: 'off-pitch note', other: 'off-pitch notes' })} highlighted in red`,
  },
  progressChart: {
    noSessions: 'No sessions yet.',
    summary: (
      sessionCount: number,
      firstScore: number,
      firstDate: string,
      lastScore: number,
      lastDate: string,
      mixedModes: boolean,
    ) =>
      `Line chart of your overall score across ${sessionCount} ` +
      `${pluralize('en', sessionCount, { one: 'session', other: 'sessions' })}, ` +
      `from ${firstScore} on ${firstDate} to ${lastScore} on ${lastDate}.` +
      (mixedModes
        ? ' Includes both a cappella and with-music sessions, marked separately -- their scores are not directly comparable.'
        : ''),
    legendClean: 'A cappella',
    legendMixed: 'With music',
  },
  progressPage: {
    heading: 'Your progress',
    loading: 'Loading your progress…',
    empty: 'Complete your first analysis to start tracking progress.',
    noChange: 'No change',
    up: (amount: number) => `Up ${amount}`,
    down: (amount: number) => `Down ${amount}`,
    latest: 'Latest',
    best: 'Best',
    average: 'Average',
    vsFirstSession: 'Vs. first session',
    mixedModesNote:
      "This history mixes a cappella and with-music sessions (marked below). They're scored " +
      "on different aspects, so scores aren't directly comparable across modes -- only compare " +
      'sessions of the same mode to each other.',
    tableCaption: 'Your analyses, most recent first',
    columnDate: 'Date',
    columnMode: 'Mode',
    columnOverallScore: 'Overall score',
    columnAction: 'Action',
    viewAnalysis: 'View',
    modeClean: 'A cappella',
    modeMixed: 'With music',
    backToHistory: 'Back to history',
    loadingSession: 'Loading this analysis…',
  },
  analysisError: {
    // Terminal analysis error codes (worker/src/vocalcoach/errors.py) --
    // shown in place of a raw "Error: CODE" (QueueStatus.tsx), the same
    // spec 8.1 "code is stable, detail is for humans" split ErrorAlert's
    // FRIENDLY_MESSAGES already uses for request-level errors.
    timeout:
      'This analysis took too long and had to be stopped. Try again -- a shorter recording usually helps.',
    internal: 'Something went wrong while analyzing this recording. Please try again.',
    referenceTooQuiet:
      "This song's reference audio couldn't be processed reliably. Try a different song.",
    noVoiceDetected:
      "We couldn't detect a singing voice in this recording. Make sure your microphone " +
      'captured your voice clearly and try again.',
    melodyExtractionFailed:
      "We couldn't reliably track your voice's melody in this recording. Try recording again " +
      'with a clearer vocal.',
    alignmentFailed:
      "This recording doesn't match the reference song closely enough to analyze. Make sure " +
      "you're recording the same song and try again.",
    alignmentTooLarge:
      'This recording is too long to compare against the reference. Try a shorter recording.',
    fallback: (code: string) => `Something went wrong (code: ${code}).`,
  },
}

export type Translations = typeof en
