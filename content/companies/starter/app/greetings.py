
# @stage: greeting endpoint=hello order=1
def build_greeting(name: str) -> dict:
    return {"message": f"Hello, {name}!"}