from flask import Flask, render_template, request, jsonify
from chat import get_response

app = Flask(__name__)

# Serve the chat UI
@app.get("/")
def index_get():
    return render_template("base.html")

# Handle messages from the frontend
@app.post("/predict")
def predict():
    text = request.get_json().get("message")
    
    # Optional: validate text
    if not text:
        return jsonify({"answer": "Sorry, I didn't get that."})

    response = get_response(text)
    message = {"answer": response}
    return jsonify(message)

# Run the Flask app in debug mode
if __name__ == "__main__":
    app.run(debug=False, port=5001)