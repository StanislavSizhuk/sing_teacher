package main

import (
	"bytes"
	"encoding/binary"
	"math"
)

// bitsPerSample, pcmChannels and pcmFormatCode fix the synthetic fixture as
// mono 16-bit PCM -- the simplest WAV ffprobe/ffmpeg accept without
// ambiguity, matching the "generated sine with known pitch" fixture style
// spec 15.2 asks unit tests to use.
const (
	bitsPerSample = 16
	pcmChannels   = 1
	pcmFormatCode = 1
	toneHz        = 440.0
	toneAmplitude = 3000
)

// syntheticWAV builds a mono 16-bit PCM WAV of a pure tone, entirely in
// memory: real audio bytes (so go-api's ffprobe/ffmpeg pass and magic-byte
// sniff both accept it), but small and silent-adjacent enough to be cheap to
// generate and upload dozens of times in a burst.
func syntheticWAV(durationSeconds float64, sampleRate int) []byte {
	numSamples := int(float64(sampleRate) * durationSeconds)
	dataSize := numSamples * (bitsPerSample / 8) * pcmChannels
	byteRate := sampleRate * pcmChannels * (bitsPerSample / 8)
	blockAlign := pcmChannels * (bitsPerSample / 8)

	buf := new(bytes.Buffer)
	buf.WriteString("RIFF")
	_ = binary.Write(buf, binary.LittleEndian, uint32(36+dataSize)) // #nosec G115 -- dataSize comes from this file's own small fixture-duration constants, never external input
	buf.WriteString("WAVE")

	buf.WriteString("fmt ")
	_ = binary.Write(buf, binary.LittleEndian, uint32(16))
	_ = binary.Write(buf, binary.LittleEndian, uint16(pcmFormatCode))
	_ = binary.Write(buf, binary.LittleEndian, uint16(pcmChannels))
	_ = binary.Write(buf, binary.LittleEndian, uint32(sampleRate)) // #nosec G115 -- sampleRate is always a small caller-supplied constant (see main.go)
	_ = binary.Write(buf, binary.LittleEndian, uint32(byteRate))   // #nosec G115 -- byteRate derives from the same small constants as sampleRate above
	_ = binary.Write(buf, binary.LittleEndian, uint16(blockAlign))
	_ = binary.Write(buf, binary.LittleEndian, uint16(bitsPerSample))

	buf.WriteString("data")
	_ = binary.Write(buf, binary.LittleEndian, uint32(dataSize)) // #nosec G115 -- dataSize comes from this file's own small fixture-duration constants, never external input

	for i := range numSamples {
		t := float64(i) / float64(sampleRate)
		sample := int16(toneAmplitude * math.Sin(2*math.Pi*toneHz*t))
		_ = binary.Write(buf, binary.LittleEndian, sample)
	}
	return buf.Bytes()
}
