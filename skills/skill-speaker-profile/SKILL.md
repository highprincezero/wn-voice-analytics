---
name: skill-speaker-profile
description: Read each speaker from the raw audio at the moment transcription starts. Return a voice signature plus estimated vocal presentation, age band, accent region, and speaking style. Estimates only. Never treat them as identity facts.
---

# Speaker profile

Use this skill when a recording is about to be transcribed. Read the audio. Do not wait for the transcript, and do not infer the voice from the words.

## When to run

Fire at the same moment the transcription model is called, on the same audio bytes, before any text exists. Transcription returns words. This skill returns the voice. Keep the two calls side by side. If several people speak, return one profile per voice, in the order they first appear.

## What to return

For each voice, return these fields:

- voice_signature: a short acoustic description of that voice on this clip. Cover pitch range, pace, loudness, and timbre. If a speaker-recognition embedding or profile id was produced from the audio, include that id. Do not invent an embedding.
- vocal_presentation: the impression the voice gives, such as lower or higher pitch, with a confidence of low, medium, or high. This is not the speaker's gender.
- age_band: a broad band such as teen, young adult, adult, or older adult, plus confidence. Do not give an exact age.
- accent_region: the language or accent the clip suggests, plus confidence. This is not the speaker's nationality.
- speaking_style: how the person sounds on this clip, such as warm, hurried, flat, or playful, plus confidence. This is not a personality diagnosis.
- evidence: one sentence naming the audible cues. Quote no transcript text, because the transcript does not exist yet.
- limits: say the clip is too short, noisy, or single-tone when the estimate is weak. Prefer "not enough voice to tell" over a guess.

## Rules

- Label every trait as an estimate.
- A short greeting is not enough for age, accent, or style. Say so.
- One voice can include more than one language. List what you hear.
- Overlapping speech stays as separate voices when you can tell them apart.
- Store the profile next to the recording. Do not overwrite the transcript with it.
