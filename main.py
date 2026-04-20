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

app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ASSEMBLYAI_API_KEY = os.environ.get('ASSEMBLYAI_API_KEY', '')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'FactLens Backend'})

@app.route('/transcribe', methods=['POST'])
def transcribe():
    try:
        data = request.json
        video_url = data.get('url', '')

        if not video_url:
            return jsonify({'error': 'No video URL provided'}), 400

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, 'audio.mp3')

            # Updated yt-dlp command with YouTube-specific fixes
            result = subprocess.run([
                'yt-dlp',
                '--extract-audio',
                '--audio-format', 'mp3',
                '--audio-quality', '5',
                '--output', audio_path,
                '--no-playlist',
                '--socket-timeout', '30',
                '--max-filesize', '15m',
                '--no-check-certificates',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '--extractor-args', 'youtube:player_client=web,web_creator',
                '--no-warnings',
                video_url
            ], capture_output=True, text=True, timeout=90)

            print('yt-dlp stdout:', result.stdout[:200])
            print('yt-dlp stderr:', result.stderr[:200])
            print('yt-dlp returncode:', result.returncode)

            # Find the downloaded file
            actual_path = audio_path
            if not os.path.exists(actual_path):
                for f in os.listdir(tmpdir):
                    if f.endswith(('.mp3', '.m4a', '.webm', '.opus')):
                        actual_path = os.path.join(tmpdir, f)
                        break

            if not os.path.exists(actual_path):
                error_msg = result.stderr[:300] if result.stderr else 'Unknown download error'
                return jsonify({'error': 'Could not download video: ' + error_msg}), 400

            print('Audio file size:', os.path.getsize(actual_path))

            if not ASSEMBLYAI_API_KEY:
                return jsonify({'error': 'AssemblyAI API key not configured'}), 500

            # Upload to AssemblyAI
            with open(actual_path, 'rb') as f:
                upload_response = requests.post(
                    'https://api.assemblyai.com/v2/upload',
                    headers={'authorization': ASSEMBLYAI_API_KEY},
                    data=f
                )

            if upload_response.status_code != 200:
                return jsonify({'error': 'Upload failed: ' + str(upload_response.status_code)}), 500

            upload_url = upload_response.json()['upload_url']

            # Request transcription
            transcript_response = requests.post(
                'https://api.assemblyai.com/v2/transcript',
                headers={
                    'authorization': ASSEMBLYAI_API_KEY,
                    'content-type': 'application/json'
                },
                json={
                    'audio_url': upload_url,
                    'language_code': 'en',
                    'punctuate': True,
                    'format_text': True
                }
            )

            transcript_id = transcript_response.json()['id']

            # Poll for completion
            for _ in range(40):
                time.sleep(3)
                poll = requests.get(
                    f'https://api.assemblyai.com/v2/transcript/{transcript_id}',
                    headers={'authorization': ASSEMBLYAI_API_KEY}
                ).json()

                if poll['status'] == 'completed':
                    return jsonify({'transcript': poll['text']})
                elif poll['status'] == 'error':
                    return jsonify({'error': 'Transcription failed: ' + poll.get('error', 'unknown')}), 500

            return jsonify({'error': 'Transcription timed out'}), 408

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Video download timed out'}), 408
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
            'This is a video transcript. Search the web to verify the specific claims made. '
            'Identify the 2 most misleading or inaccurate claims and explain what is actually true. '
            'Return ONLY valid JSON, no markdown, no citation tags:\n\n'
            'Text: "' + clean + '"\n\n'
            'Return exactly this JSON:\n'
            '{"verdict":{"type":"misleading","emoji":"warning","label":"MISLEADING","summary":"one plain English sentence"},'
            '"claims":[{"status":"warn","quote":"specific claim from transcript","explanation":"2 plain sentences of evidence.","confidence":85}],'
            '"aiGenerated":{"detected":false,"confidence":40}}\n\n'
            'Types: true=ACCURATE, false=FALSE, misleading=MISLEADING, unverified=NEEDS MORE INFO\n'
            'emoji: check/cross/warning/question. Max 2 claims. Plain text only, no HTML.'
        )

        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=800,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=[{'role': 'user', 'content': prompt}]
        )

        full_text = ''
        for block in response.content:
            if hasattr(block, 'text'):
                full_text += block.text

        stripped = full_text.replace('```json', '').replace('```', '').strip()
        match = re.search(r'\{[\s\S]*\}', stripped)
        if not match:
            return jsonify({'error': 'No JSON in response'}), 500

        result = json.loads(match.group(0))

        emoji_map = {'check': '✅', 'cross': '🚫', 'warning': '⚠️', 'question': '🔎'}
        if result.get('verdict'):
            result['verdict']['emoji'] = emoji_map.get(result['verdict']['emoji'], '🔎')

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
