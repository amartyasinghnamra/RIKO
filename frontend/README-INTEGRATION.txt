Riko frontend integration notes.

Files:
  index.html
  styles.css
  app.js
  api.js
  assets/

IMPORTANT:
The active frontend uses api.js for microphone start/stop control. Browser
MediaRecorder, character-renderer, and environment modules were archived
because the current application does not call them.

Next integration step:
connect the existing Python backend to the frontend state machine without
replacing the working dual-GPT-SoVITS pipeline.
