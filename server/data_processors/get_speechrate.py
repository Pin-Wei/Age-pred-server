#!/usr/bin/env python3
import os
from pydub import AudioSegment
from pydub.silence import split_on_silence
import whisper_timestamped as whisper
import json
from whisper_timestamped.transcribe import write_csv, flatten
import sys

def webm2wav(audio_file):
    if '.webm' in audio_file:
        out_name = audio_file.replace('.webm','.wav')
        print(out_name)
        os.system("ffmpeg -y -i "+audio_file+" "+out_name)
    else:
        out_name = audio_file
    return out_name

# Remove silence
def de_silence(audio_file, silence_len=150):
    audio = AudioSegment.from_file(audio_file)
    loudness = audio.dBFS
    print("loudness=" + str(loudness))

    chunks = split_on_silence(
        audio,
        min_silence_len=silence_len,
        silence_thresh=-40
    )

    processed_audio = AudioSegment.empty()
    for chunk in chunks:
        processed_audio += chunk

    output_file = audio_file.replace(".wav","_ds.wav")
    processed_audio.export(output_file, format="wav")
    print('Saved file: ', output_file)
    return output_file

# whisper label
def whisper_label(audio_file):
    audio = whisper.load_audio(audio_file)
    model = whisper.load_model("base", device="cpu")
    result = whisper.transcribe(
        model, audio, beam_size=5, best_of=5,
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        language="Chinese", remove_empty_words=True,
        vad=True, detect_disfluencies=True,
        remove_punctuation_from_words=True
    )
    
    csv_file = audio_file + ".words.csv"
    with open(csv_file, "w", encoding="utf-8") as csv:
        write_csv(flatten(result["segments"], "words"), file=csv)
    print('Saved file: ', csv_file)
    return csv_file

def generate_csv(audio_file):
    audio_file = webm2wav(audio_file)
    audio_file = de_silence(audio_file)
    csv_file = whisper_label(audio_file)
    return csv_file

if __name__ == "__main__":
    audio_file = sys.argv[1]
    csv_file = generate_csv(audio_file)
    print('CSV generated:', csv_file)
    print('Done.')
