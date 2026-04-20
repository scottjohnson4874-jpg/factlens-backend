# v5
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import threading
import uuid

app = Flask(__name__)
CORS(app)

jobs = {}

@app.route('/health', methods=['GET'])
def health():
    try:
        import youtube_transcript_api
        version = getattr(youtube_transcript_api, '__version__', 'unknown')
    except:
        version = 'not installed'
    return jsonify({'status': 'ok', 'service': 'FactLens Backend', 'yta_version': version})

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

        import youtube_transcript_api as yta
        print(f'YTA version: {getattr(yta, "__version__", "unknown")}')
        print(f'YTA dir: {[x for x in dir(yta) if not x.startswith("_")]}')

        api = yta.YouTubeTranscriptApi
        print(f'API methods: {[x for x in dir(api) if not x.startswith("_")]}')

        jobs[job_id] = {'status': 'error', 'error': 'Debug: check Railway logs for API methods'}

    except Exception as e:
        print(f'Error: {e}')
        jobs[job_id] = {'status': 'error', 'error': str(e)}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
