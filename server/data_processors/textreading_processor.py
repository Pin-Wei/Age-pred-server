#!/usr/bin/env python

import os
import sys

import numpy as np
import pandas as pd
from pydub import AudioSegment
from pydub.silence import split_on_silence
import whisper_timestamped as whisper
from whisper_timestamped.transcribe import write_csv, flatten

class TextReadingProcessor:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.base_path = os.path.dirname(os.path.abspath(__file__))

    def webm2wav(self, audio_file):
        if ".webm" in audio_file:
            out_file = audio_file.replace(".webm", ".wav")
            os.system(f"ffmpeg -y -i {audio_file} {out_file}")
            print("Converted .webm audio file to .wav format.")
        elif ".wav" in audio_file:
            out_file = audio_file
        else:
            raise ValueError("Unsupported audio format. Please provide a .webm or .wav file.")
        return out_file

    def de_silence(self, audio_file, silence_len=150):
        audio = AudioSegment.from_file(audio_file)
        loudness = audio.dBFS
        print(f"loudness={loudness}")

        chunks = split_on_silence(
            audio,
            min_silence_len=silence_len,
            silence_thresh=-40
        )
        processed_audio = AudioSegment.empty()
        for chunk in chunks:
            processed_audio += chunk

        out_file = audio_file.replace(".wav", "_ds.wav")
        processed_audio.export(out_file, format="wav")
        print("Successfully removed silence from audio file.")
        return out_file
    
    def whisper_label(self, audio_file):
        '''
        Generate transcription labels using Whisper.
        '''
        audio = whisper.load_audio(audio_file)
        model = whisper.load_model("base", device="cpu")
        result = whisper.transcribe(
            model, 
            audio, 
            beam_size=5, 
            best_of=5,
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            language="Chinese", 
            remove_empty_words=True,
            vad=True, 
            detect_disfluencies=True,
            remove_punctuation_from_words=True
        )
        csv_file = f"{audio_file}.words.csv"
        with open(csv_file, "w", encoding="utf-8") as csv:
            write_csv(flatten(result["segments"], "words"), file=csv)
        print("Successfully generated transcription labels using Whisper.")
        return csv_file

    def generate_csv(self, audio_file):
        try:
            audio_file = self.webm2wav(audio_file)
            audio_file = self.de_silence(audio_file)
            csv_file = self.whisper_label(audio_file)
            print(f"CSV generated: {csv_file}")
            return csv_file
        
        except Exception as e:
            print(f"Error processing audio file: {e}")
            return None
        
    def calculate_mean_syllable_speech_rate(self, csv_files):
        syllable_speech_rates = []

        for csv_file in csv_files:
            try:
                df = pd.read_csv(
                    csv_file, 
                    encoding='utf-8', 
                    usecols=[0, 1, 2], # select only the first three columns
                    names=["word", "start", "end"]
                )
                df["duration"] = df["end"] - df["start"]
                df["word_length"] = df["word"].astype(str).apply(len)
                df["syllable_sr"] = df["word_length"] / df["duration"] # speech rate per syllable
                avg_syllable_sr = df["syllable_sr"].mean() # per csv_file
                syllable_speech_rates.append(avg_syllable_sr)

            except Exception as e:
                print(f"Failed to read or process {csv_file}: {e}")
                continue

        if syllable_speech_rates:
            mean_speech_rate = np.mean(syllable_speech_rates)
            print("Successfully calculated mean syllable speech rate.")
            return mean_speech_rate
        else:
            print("No valid speech rates calculated.")
            return None
        
    def process_subject(self, subject_id):
        audio_files = [
            f for f in os.listdir(self.input_path) 
            if f.startswith(subject_id) 
            and (f.endswith(".webm") or f.endswith(".wav"))
        ]
        if audio_files:
            csv_files = []
            
            for audio_file in audio_files:
                audio_path = os.path.join(self.input_path, audio_file)
                csv_file = self.generate_csv(audio_path)

                if csv_file:
                    csv_files.append(csv_file)
                else:
                    print("No valid _ds.wav.words.csv file generated.")

            mean_speech_rate = self.calculate_mean_syllable_speech_rate(csv_files)
            if mean_speech_rate:
                output = pd.DataFrame({
                    "ID": [subject_id], 
                    "LANGUAGE_READING_BEH_NULL_MeanSR": [mean_speech_rate]
                })
                return output
            else:
                print("No valid mean speech rate calculated.")
                return None
        else:
            print(f"No audio files found for subject {subject_id}.")
            return None