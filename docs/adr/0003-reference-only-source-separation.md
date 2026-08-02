# ADR-0003: Vocal separation only for the reference track

- Status: Accepted for `clean`; amended for `mixed` by ADR-0034
- Date: 2026-07-27
- Note: the ML pipeline is built in E3, not E1. This ADR exists now because
  spec 14.3 requires it to already be on record at stage 1.
- 2026-08-02: ADR-0034 reverses this ADR's "separate both tracks" rejection
  for `mixed` mode specifically. The reasoning below stays fully valid for
  `clean` -- it was always scoped to the a cappella assumption, and `clean`
  recordings still have no accompaniment to remove.

## Context

Spec 2.3's product assumption is that the user sings a cappella in
headphones, so their own recording never has instrumental bleed to remove.
Demucs is expensive (up to 300s, spec 6.2), and it and Whisper cannot be
resident in memory at the same time on an 8GB box (spec 6.5). Running Demucs
on both the reference and the user's recording would risk both the 3-minute
wall-time budget (NFR-01) and the 6GB RAM budget (NFR-07).

## Decision

Demucs runs only on the reference track, once, cached per song by
`content_hash`/`youtube_video_id` (spec 6.6). The user's recording is
analyzed directly; a soft heuristic (spec 6.9) flags likely background-music
contamination in the report instead of blocking the result.

## Consequences

Halves the cost of the heaviest pipeline stage per analysis, and makes a
second analysis of an already-seen song cheap (cache hit). The tradeoff is a
product constraint the UI must state before recording (spec 2.3): if a user
ignores the a cappella requirement, scoring degrades rather than the
analysis failing outright.

## Alternatives considered

- Separate both tracks -- rejected: doubles Demucs cost for no accuracy gain
  under the a cappella assumption, and risks the exact octave-error problem
  Demucs exists to prevent if run on an inconsistently "pure" recording.
- Skip separation entirely, compare against the full mixed reference --
  rejected: pitch detectors misidentify octaves when bass/drums/harmony are
  present (spec 6.3), which is the reason Demucs is in the pipeline at all.
