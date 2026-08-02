from __future__ import annotations

import numpy as np

from vocalcoach.dsp.pitch_embedding import UNVOICED_TO_VOICED_DISTANCE, embed_pitch_curve


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def test_shape_and_dtype() -> None:
    embedding = embed_pitch_curve([440.0, None, 220.0])

    assert embedding.shape == (3, 2)
    assert embedding.dtype == np.float32


def test_octave_apart_pitches_embed_to_the_same_point() -> None:
    embedding = embed_pitch_curve([220.0, 440.0, 880.0, 110.0])

    for i in range(1, 4):
        assert _distance(embedding[0], embedding[i]) < 1e-4


def test_unvoiced_frame_embeds_to_the_origin() -> None:
    embedding = embed_pitch_curve([440.0, None])

    assert embedding[1, 0] == 0.0
    assert embedding[1, 1] == 0.0


def test_unvoiced_to_voiced_distance_is_the_constant_radius() -> None:
    embedding = embed_pitch_curve([440.0, None])

    assert _distance(embedding[0], embedding[1]) == UNVOICED_TO_VOICED_DISTANCE


def test_two_unvoiced_frames_have_zero_distance() -> None:
    embedding = embed_pitch_curve([None, None])

    assert _distance(embedding[0], embedding[1]) == 0.0


def test_a_different_pitch_class_is_not_degenerate_with_the_same_one() -> None:
    # A perfect fifth (700 cents), nowhere near a whole number of octaves --
    # must land clearly away from the unison/octave point, not coincide
    # with it the way an octave-apart pair does.
    root = embed_pitch_curve([220.0])[0]
    fifth = embed_pitch_curve([220.0 * (2.0 ** (700.0 / 1200.0))])[0]

    assert _distance(root, fifth) > 1.0
