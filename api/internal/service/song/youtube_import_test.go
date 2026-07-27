package song_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/youtube"
)

func TestAddFromYouTube_Disabled_Rejected(t *testing.T) {
	yt := &fakeYouTubeClient{}
	svc := newTestService(t, newFakeRepository(), &fakeAudioProcessor{}, yt, 360, false)

	_, _, err := svc.AddFromYouTube(context.Background(), "https://youtu.be/x", "")
	require.ErrorIs(t, err, domain.ErrYouTubeImportDisabled)
	require.Equal(t, 0, yt.metadataCalls)
}

func TestAddFromYouTube_InvalidURL_Rejected(t *testing.T) {
	yt := &fakeYouTubeClient{}
	svc := newTestService(t, newFakeRepository(), &fakeAudioProcessor{}, yt, 360, true)

	_, _, err := svc.AddFromYouTube(context.Background(), "https://vimeo.com/12345", "")
	require.ErrorIs(t, err, domain.ErrInvalidYouTubeURL)
	require.Equal(t, 0, yt.metadataCalls)
}

func TestAddFromYouTube_TooLong_RejectedBeforeDownload(t *testing.T) {
	yt := &fakeYouTubeClient{info: youtube.VideoInfo{ID: "abc123", Title: "Long Video", DurationSeconds: 500}}
	svc := newTestService(t, newFakeRepository(), &fakeAudioProcessor{}, yt, 360, true)

	_, _, err := svc.AddFromYouTube(context.Background(), "https://youtu.be/abc123", "")
	require.ErrorIs(t, err, domain.ErrYouTubeVideoTooLong)
	require.Equal(t, 0, yt.downloadCalls, "duration must be checked before any download (FR-12)")
}

func TestAddFromYouTube_MetadataError_Propagated(t *testing.T) {
	yt := &fakeYouTubeClient{metadataErr: errBoom}
	svc := newTestService(t, newFakeRepository(), &fakeAudioProcessor{}, yt, 360, true)

	_, _, err := svc.AddFromYouTube(context.Background(), "https://youtu.be/x", "")
	require.ErrorIs(t, err, errBoom)
}

func TestAddFromYouTube_New_DownloadsAndTranscodes(t *testing.T) {
	repo := newFakeRepository()
	processor := &fakeAudioProcessor{}
	yt := &fakeYouTubeClient{info: youtube.VideoInfo{ID: "abc123", Title: "A Song", DurationSeconds: 200}}
	svc := newTestService(t, repo, processor, yt, 360, true)

	got, reused, err := svc.AddFromYouTube(context.Background(), "https://youtu.be/abc123", "")
	require.NoError(t, err)
	require.False(t, reused)
	require.Equal(t, "A Song", got.Title)
	require.Equal(t, domain.SongSourceYouTube, got.SourceType)
	require.Equal(t, "youtube:abc123", got.ContentHash)
	require.Equal(t, 1, yt.downloadCalls)
	require.Equal(t, 1, processor.transcodeCalls)
}

func TestAddFromYouTube_TitleOverride_Used(t *testing.T) {
	repo := newFakeRepository()
	yt := &fakeYouTubeClient{info: youtube.VideoInfo{ID: "abc123", Title: "Original Title", DurationSeconds: 100}}
	svc := newTestService(t, repo, &fakeAudioProcessor{}, yt, 360, true)

	got, _, err := svc.AddFromYouTube(context.Background(), "https://youtu.be/abc123", "My Custom Title")
	require.NoError(t, err)
	require.Equal(t, "My Custom Title", got.Title)
}

func TestAddFromYouTube_DedupHit_SkipsDownload(t *testing.T) {
	repo := newFakeRepository()
	yt := &fakeYouTubeClient{info: youtube.VideoInfo{ID: "abc123", Title: "A Song", DurationSeconds: 200}}
	svc := newTestService(t, repo, &fakeAudioProcessor{}, yt, 360, true)
	ctx := context.Background()

	first, _, err := svc.AddFromYouTube(ctx, "https://youtu.be/abc123", "")
	require.NoError(t, err)
	require.Equal(t, 1, yt.downloadCalls)

	second, reused, err := svc.AddFromYouTube(ctx, "https://youtu.be/abc123", "")
	require.NoError(t, err)
	require.True(t, reused)
	require.Equal(t, first.ID, second.ID)
	require.Equal(t, 1, yt.downloadCalls, "a dedup hit must never re-download (spec 6.6 cache key)")
}

func TestAddFromYouTube_DownloadError_Propagated(t *testing.T) {
	repo := newFakeRepository()
	yt := &fakeYouTubeClient{
		info:        youtube.VideoInfo{ID: "abc123", Title: "A Song", DurationSeconds: 100},
		downloadErr: errBoom,
	}
	svc := newTestService(t, repo, &fakeAudioProcessor{}, yt, 360, true)

	_, _, err := svc.AddFromYouTube(context.Background(), "https://youtu.be/abc123", "")
	require.ErrorIs(t, err, errBoom)
}

func TestAddFromYouTube_DownloadedContentUnsupported_Rejected(t *testing.T) {
	repo := newFakeRepository()
	yt := &fakeYouTubeClient{
		info:          youtube.VideoInfo{ID: "abc123", Title: "A Song", DurationSeconds: 100},
		downloadBytes: []byte("not actually audio"),
	}
	svc := newTestService(t, repo, &fakeAudioProcessor{}, yt, 360, true)

	_, _, err := svc.AddFromYouTube(context.Background(), "https://youtu.be/abc123", "")
	require.ErrorIs(t, err, domain.ErrUnsupportedAudioFormat)
}
