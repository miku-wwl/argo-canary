from flask import Flask
import random

app = Flask(__name__)

@app.route("/")
def home():
    # Working version
    return "Hello Auckland k8s Demo - From version 49"

    #New version with simulated failure
    # if random.random() < 0.5:  # 50% chance to fail
    #     return "simulated failure", 500
    # return "Hello Auckland k8s Demo - From version 46"

@app.route("/health")
def health():
    return "ok"

@app.route("/error")
def error():
    if random.random() < 0.3:
        return "error", 500
    return "ok"
    
app.run(host="0.0.0.0", port=5000)