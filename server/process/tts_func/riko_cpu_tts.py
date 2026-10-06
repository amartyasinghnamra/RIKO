from tts.gpt_sovits import synthesize


def riko_cpu_tts(in_text, output_wav_pth="output.wav"):
    return synthesize(in_text, output_wav_pth)


if __name__ == "__main__":
    result = riko_cpu_tts(
        "Hello, this is your voice companion speaking.",
        "/tmp/riko_cpu_standalone.wav",
    )

    print("Result:", result)
