from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import os
import subprocess
import json
import re
import tempfile
import requests
import time
import threading
import uuid

app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ASSEMBLYAI_API_KEY = os.environ.get('ASSEMBLYAI_API_KEY', '')

# Store jobs in memory
jobs = {}

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'service': 'FactLens Backend',
        'assemblyai_key_set': bool(ASSEMBLYAI_API_KEY),
        'assemblyai_key_length': len(ASSEMBLYAI_API_KEY)
    })

@app.route('/test-pytubefix', methods=['GET'])
def test_pytubefix():
    """Test if pytubefix can download a YouTube video"""
    try:
        from pytubefix import YouTube
        url = 'https://www.youtube.com/watch?v=9hzrN-Jb10A'
        yt = YouTube(url)
        streams = yt.streams.filter(only_audio=True)
        stream_info = [{'itag': s.itag, 'mime': s.mime_type, 'abr': s.abr} for s in streams]
        return jsonify({'status': 'ok', 'title': yt.title, 'streams': stream_info[:3]})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/transcribe/start', methods=['POST'])
def transcribe_start():
    try:
        data = request.json
        video_url = data.get('url', '')
        cookies = data.get('cookies', '')

        if not video_url:
            return jsonify({'error': 'No video URL provided'}), 400

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {'status': 'processing', 'transcript': None, 'error': None}

        thread = threading.Thread(target=do_transcription, args=(job_id, video_url, cookies))
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

def do_transcription(job_id, video_url, cookies):
    try:
        if not ASSEMBLYAI_API_KEY:
            jobs[job_id] = {'status': 'error', 'error': 'No AssemblyAI key configured'}
            return

        # Submit YouTube URL directly to AssemblyAI — no downloading needed
        print(f'Submitting to AssemblyAI directly: {video_url}')

        transcript_response = requests.post(
            'https://api.assemblyai.com/v2/transcript',
            headers={'authorization': ASSEMBLYAI_API_KEY, 'content-type': 'application/json'},
            json={
                'audio_url': video_url,
                'language_code': 'en',
                'punctuate': True
            }
        )

        resp_json = transcript_response.json()
        print(f'AssemblyAI response: {resp_json}')

        if 'id' not in resp_json:
            jobs[job_id] = {'status': 'error', 'error': 'AssemblyAI rejected URL: ' + str(resp_json.get('error', 'unknown'))}
            return

        transcript_id = resp_json['id']
        print(f'Transcription started: {transcript_id}')

        # Poll for completion
        for i in range(40):
            time.sleep(3)
            poll = requests.get(
                f'https://api.assemblyai.com/v2/transcript/{transcript_id}',
                headers={'authorization': ASSEMBLYAI_API_KEY}
            ).json()

            print(f'Poll {i+1}: {poll["status"]}')

            if poll['status'] == 'completed':
                jobs[job_id] = {'status': 'done', 'transcript': poll['text']}
                return
            elif poll['status'] == 'error':
                jobs[job_id] = {'status': 'error', 'error': 'AssemblyAI error: ' + poll.get('error', 'unknown')}
                return

        jobs[job_id] = {'status': 'error', 'error': 'Transcription timed out'}

    except Exception as e:
        print(f'Transcription error: {e}')
        jobs[job_id] = {'status': 'error', 'error': str(e)}


def _download_with_ytdlp(video_url, audio_path, tmpdir, cookies):
    cmd = [
        'yt-dlp',
        '--extract-audio',
        '--audio-format', 'mp3',
        '--audio-quality', '5',
        '--output', audio_path,
        '--no-playlist',
        '--socket-timeout', '30',
        '--max-filesize', '15m',
        '--no-check-certificates',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    ]

    if cookies:
        cookies_path = os.path.join(tmpdir, 'cookies.txt')
        with open(cookies_path, 'w') as f:
            f.write('# Netscape HTTP Cookie File\n')
            for pair in cookies.split('; '):
                if '=' in pair:
                    name, _, value = pair.partition('=')
                    f.write(f'.youtube.com\tTRUE\t/\tFALSE\t0\t{name.strip()}\t{value.strip()}\n')
        cmd.extend(['--cookies', cookies_path])

    cmd.append(video_url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    print(f'yt-dlp exit: {result.returncode}, stderr: {result.stderr[:200]}')


@app.route('/factcheck', methods=['POST'])
def factcheck():
    try:
        data = request.json
        text = data.get('text', '')

        if not text:
            return jsonify({'error': 'No text provided'}), 400

        clean = text[:1500]
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = (
            'You are FactLens, an expert fact-checker. Today is April 2026. '
            'This is a video transcript. Search the web to verify the specific claims. '
            'Identify the 2 most misleading or inaccurate claims and explain what is actually true. '
            'Return ONLY valid JSON, no markdown, no citation tags:\n\n'
            'Text: "' + clean + '"\n\n'
            'Return exactly this JSON:\n'
            '{"verdict":{"type":"misleading","emoji":"warning","label":"MISLEADING","summary":"one plain sentence"},'
            '"claims":[{"status":"warn","quote":"specific claim","explanation":"2 plain sentences.","confidence":85}],'
            '"aiGenerated":{"detected":false,"confidence":40}}\n\n'
            'Types: true=ACCURATE, false=FALSE, misleading=MISLEADING, unverified=NEEDS MORE INFO\n'
            'emoji: check/cross/warning/question. Max 2 claims. Plain text only.'
        )

        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=1000,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=[{'role': 'user', 'content': prompt}]
        )

        full_text = ''
        for block in response.content:
            if hasattr(block, 'text'):
                full_text += block.text

        stripped = full_text.replace('```json', '').replace('```', '').strip()
        start = stripped.find('{')
        end = stripped.rfind('}')
        if start == -1 or end == -1:
            return jsonify({'error': 'No JSON in response'}), 500

        result = json.loads(stripped[start:end+1])

        emoji_map = {'check': '✅', 'cross': '🚫', 'warning': '⚠️', 'question': '🔎'}
        if result.get('verdict'):
            result['verdict']['emoji'] = emoji_map.get(result['verdict']['emoji'], '🔎')

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
