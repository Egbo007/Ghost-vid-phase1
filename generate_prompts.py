import argparse
import json
import os
import sys

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Subject-first visual baseline definitions
ADA_BASE = "A 25-year-old African woman, Ada, with a sharp scar above her left eyebrow, wearing dark apocalyptic survival gear,"
ZION_BASE = "A 28-year-old African man, Zion, with a serious unsmiling face and short dark hair, wearing a tactical vest,"
BOTH_BASE = "A 25-year-old African woman Ada with an eyebrow scar alongside a 28-year-old African man Zion,"

SCENE_PROMPTS = {
    "scene_01a": f"{ADA_BASE} standing on a cracked overgrown highway, looking across a ruined city, dramatic atmospheric lighting, photorealistic, 8k",
    "scene_01b": f"{ADA_BASE} scanning the horizon, intense survival posture, desolate dirt road, sharp focus, cinematic lighting, 8k resolution",
    "scene_02a": f"{ZION_BASE} holding a small radio communicator inside a ruined building, dusty shadows, muted sunlight, realistic, 8k",
    "scene_02b": f"{ZION_BASE} studying a worn paper map spread on a wooden table, intense focused expression, dim indoor lighting, photorealistic",
    "scene_03a": f"{BOTH_BASE} walking cautiously through a foggy urban street, post-apocalyptic background, overgrown vegetation, 8k cinematic",
    "scene_03b": f"{BOTH_BASE} taking cover behind a rusted barricade, dramatic face shadows, sharp focus, 8k depth of field",
    "scene_04a": f"{ADA_BASE} holding a hand torch inside a dark concrete industrial corridor, beam of light on face, high contrast, photorealistic",
    "scene_04b": f"{ADA_BASE} examining electronic equipment on a shelf, subtle glow reflecting on skin, ultra detailed, 8k",
    "scene_05a": f"{ZION_BASE} standing guard at an open doorway holding a metal bar, long sunlight shadows, dramatic cinematic angle, photorealistic",
    "scene_05b": f"{ZION_BASE} shielding his eyes while looking into the bright apocalyptic sun, atmospheric post-apocalyptic shot, 8k",
    "scene_06a": f"{BOTH_BASE} standing together on a hill at sunset overlooking a ruined landscape, warm golden hour light, cinematic 8k quality",
    "scene_06b": f"{BOTH_BASE} walking forward towards the camera into twilight, determined expressions, highly detailed, photorealistic",
    "episode_01_thumbnail": f"Close-up dramatic portrait of {ADA_BASE} and {ZION_BASE}, post-apocalyptic survival aesthetic, sharp detail, 8k poster style"
}

def generate_prompts(episode):
    output_dir = os.path.join("ashes-of-the-chosen", f"episode-{episode:02d}")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"episode_{episode:02d}_prompts.txt")
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(SCENE_PROMPTS, f, indent=4)
        
    print(f"✅ Prompts successfully generated at {filepath}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=int, default=1)
    args = parser.parse_args()
    generate_prompts(args.episode)
