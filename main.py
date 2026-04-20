# v3
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import re
import requests
import time
import threading
import uuid

app = Flask(__name__)
CORS(app)

ASSEMBLYAI_API_KEY = os.environ.get('ASSEMBLYAI_API_KEY', '') or '6ebcaec19ec14b90a91a1371f0a50c7f'

jobs = {}

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'FactLens Backend'})

@app.route('/transcribe/start', methods=['POST'])
def transcribe_start():
    try:
        data = request.json
        video_url = data.get('url', '')
        if not video_url:
            return jsonify({'error': 'No video URL provided'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'processing'}

        thread = threading.Thread(target=do_transcription, args=(job_id, video_url))
        thread.daemon = True
        thread.start()

        return jsonify({'job_id': job_id, 'status': 'processing'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/transcribe/status/<job_id>', methods=['GET'])
def transcribe_status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    job = jobs[job_id]

    if job['status'] == 'done':
        transcript = job['transcript']
        del jobs[job_id]
        return jsonify({'status': 'done', 'transcript': transcript})
    elif job['status'] == 'error':
        error = job['error']
        del jobs[job_id]
        return jsonify({'status': 'error', 'error': error}), 400
    else:
        return jsonify({'status': 'processing'})

def do_transcription(job_id, video_url):
    try:
        vid_match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', video_url)
        if not vid_match:
            jobs[job_id] = {'status': 'error', 'error': 'Could not extract video ID'}
            return

        video_id = vid_match.group(1)
        print(f'Getting transcript for: {video_id}')

        from youtube_transcript_api import YouTubeTranscriptApi
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'en-US', 'en-GB', 'en-AU'])
        transcript = ' '.join([t['text'] for t in transcript_list])
        print(f'Transcript length: {len(transcript)} chars')
        print(f'Transcript preview: {transcript[:200]}')

        if len(transcript) > 50:
            jobs[job_id] = {'status': 'done', 'transcript': transcript}
        else:
            jobs[job_id] = {'status': 'error', 'error': 'Transcript too short'}

    except Exception as e:
        print(f'Transcription error: {e}')
        jobs[job_id] = {'status': 'error', 'error': str(e)}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
