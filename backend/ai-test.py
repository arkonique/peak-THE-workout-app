import os
import json
from typing import List, Optional
from pydantic import BaseModel, Field, ValidationError
from google import genai
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing from .env.")

gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
if not gemini_model:
    raise RuntimeError("GEMINI_MODEL cannot be empty.")


# 1. Initialize the client (Reads GEMINI_API_KEY from environment vars)
client = genai.Client(api_key=api_key)

# ---------------------------------------------------------
# STEP 2: Define your precise JSON Output Schema
# ---------------------------------------------------------
class ExerciseObject(BaseModel):
    exercise_id: int = Field(description="The exact database ID matching the selected exercise from the Supabase subset.")
    exercise_name: str = Field(description="The name of the exercise.")
    reasoning: str = Field(description="A few sentences explaining why this specific exercise was selected for the user.")

class WorkoutObject(BaseModel):
    workout_name: str = Field(description="Name of the routine (e.g., Upper Body Power, Quick Leg Routine).")
    exercises: List[ExerciseObject] = Field(description="Array of targeted exercises from the permitted list.")

class FoodObject(BaseModel):
    name: str = Field(description="Name of the food item.")
    quantity: str = Field(description="The portion size or quantity (e.g., 200g, 2 large, 1 cup).")
    reasoning: str = Field(description="A few sentences explaining the nutritional purpose of this item.")

class MealObject(BaseModel):
    meal_name: str = Field(description="Name of the meal (e.g., Breakfast, Post-Workout Shake, Dinner).")
    foods: List[FoodObject]

class MealPlanResponse(BaseModel):
    workout: Optional[WorkoutObject] = Field(default=None, description="The workout routine, if requested or relevant to the prompt.")
    meals: Optional[List[MealObject]] = Field(default=None, description="The generic or customized meal plan array.")


def _json_object_candidates(text: str):
    """Yield complete JSON objects embedded in an otherwise textual response."""

    for start, character in enumerate(text):
        if character != "{":
            continue

        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            character = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue

            if character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : end + 1]
                    break


def _parse_structured_response(output_text: str) -> MealPlanResponse:
    """Validate structured output, including JSON wrapped in prose or fences."""

    text = (output_text or "").strip()
    candidates = [text, *_json_object_candidates(text)]
    seen = set()

    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return MealPlanResponse.model_validate_json(candidate)
        except ValidationError:
            continue

    preview = " ".join(text.split())[:200] or "<empty response>"
    raise RuntimeError(
        f"{gemini_model} returned non-JSON output despite the requested "
        f"response format: {preview!r}"
    )


def _content_text(content: object) -> str:
    """Extract text from an Interactions API content value."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    text_parts = []
    for part in content:
        if isinstance(part, dict):
            text = part.get("text")
        else:
            text = getattr(part, "text", None)
        if text:
            text_parts.append(str(text))
    return "".join(text_parts)


def _interaction_history(interaction: object) -> List[dict]:
    """Convert the current Interactions API steps into simple chat messages."""

    history = []
    for step in getattr(interaction, "steps", None) or []:
        if isinstance(step, dict):
            step_type = step.get("type")
            content = step.get("content")
        else:
            step_type = getattr(step, "type", None)
            content = getattr(step, "content", None)

        role = {"user_input": "user", "model_output": "model"}.get(step_type)
        text = _content_text(content)
        if role and text:
            history.append({"role": role, "text": text})
    return history

# ---------------------------------------------------------
# STEP 3: Core API Function handling Sessions & Context
# ---------------------------------------------------------
def process_fitness_chat(
    user_prompt: str, 
    session_id: str = "-1", 
    supabase_exercise_subset: Optional[List[dict]] = None
) -> dict:
    """
    Handles multi-turn stateful fitness generation.
    
    Args:
        user_prompt: The incoming message from your app user.
        session_id: The Google Interaction string ID. Pass "-1" to start a new chat.
        supabase_exercise_subset: A pre-filtered list of exercises from your DB.
    """
    
    # Base instructions telling the AI who it is and how to treat your DB ingredients
    system_instruction = (
        "You are an expert all-rounder fitness personal trainer and nutritionist. "
        "Your task is to build workouts, individual exercise recommendations, or meal plans "
        "based strictly on what the user asks.\n\n"
        "CRITICAL FOR WORKOUTS:\n"
        "You must ONLY select exercises from the provided inventory subset. "
        "Match your choices directly to their corresponding IDs. Do not make up exercises."
    )
    
    # Format the prompt payload. Inject your DB subset as static context variables.
    formatted_input = f"{user_prompt}"
    if supabase_exercise_subset:
        formatted_input += f"\n\n### PERMITTED EXERCISE INVENTORY SUBSET:\n{json.dumps(supabase_exercise_subset)}"

    # The Interactions API takes configuration as top-level fields. The
    # GenerateContentConfig/config= form belongs to models.generate_content().
    interaction_args = {
        "model": gemini_model,
        "input": formatted_input,
        "system_instruction": system_instruction,
        "generation_config": {"temperature": 0.3},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": MealPlanResponse.model_json_schema(),
        },
    }
    if session_id != "-1" and session_id:
        interaction_args["previous_interaction_id"] = session_id

    interaction = client.interactions.create(**interaction_args)
        
    # Parse out the JSON payload string into a clean Python dictionary
    structured_response = _parse_structured_response(interaction.output_text)
    structured_json = structured_response.model_dump(exclude_none=True)
    
    # Fetch previous logs associated with this server state to return to your front-end
    chat_log_history = []
    try:
        # Querying the session's internal conversation thread
        history_data = client.interactions.get(id=interaction.id, include_input=True)
        chat_log_history = _interaction_history(history_data)
    except Exception:
        # Fallback if list indexing changes; ensures app doesn't crash
        chat_log_history = [{"role": "user", "text": user_prompt}, {"role": "model", "text": interaction.output_text}]

    if not chat_log_history:
        chat_log_history = [
            {"role": "user", "text": user_prompt},
            {"role": "model", "text": interaction.output_text},
        ]

    # Build the final interface package your mobile/web app needs
    return {
        "current_session_id": interaction.id, # Save this string back into your Supabase session column!
        "data": structured_json,             # This contains the perfectly structured workout/meals objects
        "chat_history_log": chat_log_history # Array containing past statements for rendering logs UI
    }

# ---------------------------------------------------------
# STEP 4: Execution / Verification Example
# ---------------------------------------------------------
if __name__ == "__main__":
    # Mocking a lightweight select query from your 873 rows in Supabase
    mocked_supabase_rows = [
        {"id": 41, "name": "DB Goblet Squat", "muscle": "Quads"},
        {"id": 92, "name": "Romanian Deadlift", "muscle": "Hamstrings"},
        {"id": 104, "name": "Standing Calf Raises", "muscle": "Calves"},
        {"id": 210, "name": "Pushups", "muscle": "Chest"},
    ]

    print("--- FIRST TURN: Creating a New Session ---")
    response_turn_1 = process_fitness_chat(
        user_prompt="I want a short leg routine and a high protein breakfast idea.",
        session_id="-1",
        supabase_exercise_subset=mocked_supabase_rows
    )
    
    # Capture the generated Server ID to mimic app closing/reopening
    saved_server_session_id = response_turn_1["current_session_id"]
    print(f"Server Session ID Created: {saved_server_session_id}")
    print("AI Structured Response:")
    print(json.dumps(response_turn_1["data"], indent=2))
    
    print("\n--- SECOND TURN: Continuing the Session later ---")
    response_turn_2 = process_fitness_chat(
        user_prompt="That's perfect. Can you adjust the breakfast to be dairy free? Also keep the exact same legs routine.",
        session_id=saved_server_session_id,
        supabase_exercise_subset=mocked_supabase_rows
    )
    
    print("AI Adjusted Response (Dairy Free):")
    print(json.dumps(response_turn_2["data"], indent=2))
