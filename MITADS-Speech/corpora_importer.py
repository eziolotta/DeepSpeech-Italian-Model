
#!/usr/bin/env python3

#from typing import List, Set, Dict, Tuple, Optional
import requests
import time
import os
from os import path, makedirs 
import random
import re
import logging
import progressbar 
from zipfile import ZipFile
from multiprocessing import Pool
import subprocess
from utils.downloader import SIMPLE_BAR, maybe_download
from collections import Counter
logging.basicConfig(level=logging.DEBUG)
import sox

SAMPLE_RATE = 16000
BITDEPTH = 16
N_CHANNELS = 1
MAX_SECS = 15 ##20
MIN_SECS = 0 # 1 ##zero second audio (probably) means one-word speech

AUDIO_EXTENSIONS = [".wav", ".mp3"]
AUDIO_WAV_EXTENSIONS = [".wav"]
AUDIO_MP3_EXTENSIONS = [".mp3"]

def is_audio_file(filepath):
    return any(
        os.path.basename(filepath).lower().endswith(extension) for extension in AUDIO_EXTENSIONS
    )

def is_audio_mp3_file(filepath):
    return any(
        os.path.basename(filepath).lower().endswith(extension) for extension in AUDIO_MP3_EXTENSIONS
    )

def is_audio_wav_file(filepath):
    return any(
        os.path.basename(filepath).lower().endswith(extension) for extension in AUDIO_WAV_EXTENSIONS
    )

def string_escape(s, encoding='utf-8'):

    try:        
        return (s.encode('latin1')         # To bytes, required by 'unicode-escape'
                .decode('unicode-escape') # Perform the actual octal-escaping decode
                .encode('latin1')         # 1:1 mapping back to bytes
                .decode(encoding))        # Decode original encoding
    except:
        return (s.encode('utf-8')  ## cp1252     
                .decode('unicode-escape') 
                .encode('utf-8')  
                .decode(encoding))   

def get_counter():
    return Counter({'all': 0, 'failed': 0, 'invalid_label': 0, 'too_short': 0, 'too_long': 0, 'imported_time': 0, 'total_time': 0})


def _maybe_convert_mp3_to_wav(mp3_filename, wav_filename):
    import sox
    if not os.path.exists(wav_filename):
        transformer = sox.Transformer()
        transformer.convert(samplerate=SAMPLE_RATE, n_channels=CHANNELS)
        try:
            transformer.build(mp3_filename, wav_filename)
        except sox.core.SoxError:
            pass

class Corpus:
    def __init__(self,utterences:dict,audios:list,datasets_sizes = [0.8,0.1,0.1],make_wav_resample=False): 

        ##utterences:dict --> key: audio_file_full_path  value:utterance 
        ## audios is a list of all audio_file , full absolute path
        ## datasets_sizes: dimension of train-test-dev datasets (in different csv)
        self.utterences = utterences
        self.audios = audios
        self.datasets_sizes = datasets_sizes
        self.make_wav_resample = make_wav_resample


class ArchiveImporter:
    def __init__(self,corpus_name,archive_url,data_dir=None,csv_append_mode=False):
        self.corpus_name=corpus_name
        self.archive_url=archive_url
        ##Make archive_name from archive_filename
        archive_filename = self.archive_url.rsplit('/', 1)[-1]    
        self.archive_name = archive_filename.rsplit('.', 1)[0]
        # Making path absolute root data or prefered from param data_dir
        self.dataset_path = os.path.abspath(self.corpus_name) if data_dir==None else  os.path.join(data_dir, self.corpus_name)
        
        self.origin_data_path = os.path.join(self.dataset_path, "origin") if data_dir==None else  data_dir
        
        self.dataset_output_path = os.path.abspath(self.corpus_name)
        self.csv_append_mode = csv_append_mode

    def run(self):
        self._download_and_preprocess_data()

    def _download_and_preprocess_data(self):

        if not path.exists(self.dataset_output_path):
            print('No path "%s" - creating ...' % self.dataset_output_path)
            makedirs(self.dataset_output_path)

        archive_filename = self.archive_url.rsplit('/', 1)[-1]
        # Conditionally download data
        archive_path = maybe_download(archive_filename, self.origin_data_path, self.archive_url)
        # Conditionally extract common voice data
        self._maybe_extract(self.origin_data_path, self.archive_name, archive_path)
       
        ##get corpus:  audio_file_names + transcriptions
        print('Filter audio file and parse transcript...')
        corpus = self.get_corpus()

         # Conditionally convert CSV files and mp3/wav data to DeepSpeech CSVs and wav
        self._maybe_convert_sets(corpus)      

    def _maybe_extract(self,target_dir, extracted_data, archive_path):
        # If target_dir/extracted_data does not exist, extract archive in target_dir
        extracted_path = os.path.join(target_dir, extracted_data)
        if not os.path.exists(extracted_path):
            print(f"No directory {extracted_path} - extracting archive...")
            with ZipFile(archive_path, "r") as zipobj:
                # Extract all the contents of zip file in current directory
                zipobj.extractall(target_dir)
        else:
            print(f"Found directory {extracted_path} - not extracting it from archive.")


    ##override this to use full functionality
    def get_corpus(self) -> Corpus :    
        print('must be implemented in importer')


    def _maybe_convert_sets(self,corpus:Corpus):

        samples = corpus.audios
        num_samples = len(samples)
        if(num_samples==0):
            return

        if(corpus.make_wav_resample):
                        
            # Mutable counters for the concurrent embedded routine
            counter = get_counter()
            print(f"Converting wav/mp3 files to wav {SAMPLE_RATE}hz...")
            pool = Pool()
            bar = progressbar.ProgressBar(max_value=num_samples, widgets=SIMPLE_BAR)
            rows = []
            for i, processed in enumerate(pool.imap_unordered(self.one_sample,samples), start=1):
                counter += processed[0]
                rows += processed[1]
                bar.update(i)
            bar.update(num_samples)
            pool.close()
            pool.join()
            
            ## rows contains wav filenames filtered
            filenames_filtered = [r[0] for r in rows]
            for f in corpus.audios:
                if f not in filenames_filtered:
                    ##remove item
                    corpus.audios.remove(f)
                    del corpus.utterences[f]
            ########################################

        self._write_csv(corpus)

    def _maybe_convert_wav(self,mp3_filename, wav_filename):
        if not os.path.exists(wav_filename):
            transformer = sox.Transformer()
            transformer.convert(samplerate=SAMPLE_RATE,n_channels=CHANNELS, bitdepth=BITDEPTH)
            try:
                transformer.build(str(mp3_filename), str(wav_filename))
            except sox.core.SoxError:
                pass

    def one_sample(self,sample):
        mp3_wav_filename = sample
        # Storing wav files next to the mp3 ones - just with a different suffix
        wav_filename = path.splitext(mp3_wav_filename)[0] + ".wav"
        self._maybe_convert_wav(mp3_wav_filename, wav_filename)
              
       

        #frames = int(
        #    subprocess.check_output(["soxi", "-s", wav_filename], stderr=subprocess.STDOUT)
        #)
        file_size = -1
        ##frames = None
        frames = -1
        if os.path.exists(wav_filename):
            file_size = path.getsize(wav_filename)

            ##to get frames/duration for mp3/wav audio we not use soxi command but sox.file_info.duration(
            ##soxi command is not present in Windows sox distribution  - see this  https://github.com/rabitt/pysox/pull/74
            duration = sox.file_info.duration(wav_filename)
            frames = duration * SAMPLE_RATE

        label = '' ##label not managed validate_label(sample[1])
        rows = []
        counter = get_counter()
        if file_size == -1:
            # Excluding samples that failed upon conversion
            print(f'Conversion failed {mp3_wav_filename}')
            counter["failed"] += 1
        elif label is None:
            # Excluding samples that failed on label validation
            counter["invalid_label"] += 1
        elif int(frames / SAMPLE_RATE * 1000 / 10 / 2) < len(str(label)):
            # Excluding samples that are too short to fit the transcript
            counter["too_short"] += 1
        elif frames / SAMPLE_RATE > MAX_SECS:
            # Excluding very long samples to keep a reasonable batch-size
            print(f' Clips too long, {str(frames / SAMPLE_RATE)}  - {mp3_wav_filename}')

            counter["too_long"] += 1
        else:
            # This one is good - keep it for the target CSV
            rows.append((wav_filename, file_size, label))
            counter["imported_time"] += frames
        counter["all"] += 1
        counter["total_time"] += frames
        return (counter, rows)
    

    def _write_csv(self,corpus:Corpus):

        print(f"Writing CSV file")
        audios = corpus.audios
        utterences = corpus.utterences
        csv = []

        samples_len = len(audios)
        for _file in audios:

            st = os.stat(_file)
            file_size = st.st_size
            utterence = utterences[_file]
            utterence_clean = utterence      
            ##make relative path audio file
            _file_relative_path =  _file.replace(self.origin_data_path,'') 
            _file_relative_path = ''.join(['/' if c=='\\' or c=='/' else c for c in _file_relative_path])[1:]

            csv_line = f"{_file_relative_path},{file_size},{utterence_clean}\n"
            csv.append(csv_line)

        #shuffle set
        random.seed(76528)
        random.shuffle(csv)

        train_len = int(samples_len*corpus.datasets_sizes[0])
        test_len = int(samples_len*corpus.datasets_sizes[1])
        if(samples_len<train_len+test_len):
            raise('size of the test dataset must be less than {}'.format(str(samples_len-train_len)))

        dev_len = samples_len - train_len - test_len
        train_data = csv[:train_len]
        dev_data = csv[train_len:train_len+test_len]
        test_data = csv[train_len+test_len:]

        file_open_mode = 'a' if self.csv_append_mode else 'w'
        with open(os.path.join(self.dataset_output_path, "train_full.csv"), file_open_mode,encoding='utf-8') as fd:
            
            if(not self.csv_append_mode):
                fd.write("wav_filename,wav_filesize,transcript\n")
            
            for i in csv:
                fd.write(i)
        with open(os.path.join(self.dataset_output_path, "train.csv"), file_open_mode,encoding='utf-8') as fd:
            if(not self.csv_append_mode):
                fd.write("wav_filename,wav_filesize,transcript\n")
            for i in train_data:
                fd.write(i)
        with open(os.path.join(self.dataset_output_path, "dev.csv"), file_open_mode,encoding='utf-8') as fd:
            if(not self.csv_append_mode):
                fd.write("wav_filename,wav_filesize,transcript\n")
            for i in dev_data:
                fd.write(i)
        with open(os.path.join(self.dataset_output_path, "test.csv"), file_open_mode,encoding='utf-8') as fd:
            if(not self.csv_append_mode):
                fd.write("wav_filename,wav_filesize,transcript\n")
            for i in test_data:
                fd.write(i)

        print(f"Wrote {len(csv)} entries")


    

#if __name__ == "__main__":

 