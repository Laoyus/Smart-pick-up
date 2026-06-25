from flask import Flask
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

esp = None
web = None


HTML = """
<!DOCTYPE html>
<html>

<body>

<h2>ESP32 Terminal</h2>

<input id="txt" type="text">
<button onclick="sendMsg()">Send</button>

<div id="log"></div>

<script>

let ws=new WebSocket("ws://"+location.host+"/web");

const log=document.getElementById("log");

function print(x)
{
    log.innerHTML+="<p>"+x+"</p>";
}

ws.onmessage=(e)=>
{
    print("ESP : "+e.data);
}

function sendMsg()
{
    let t=document.getElementById("txt");

    ws.send(t.value);

    print("WEB : "+t.value);

    t.value="";
}

</script>

</body>

</html>
"""


@app.route("/")
def index():
    return HTML


@sock.route("/esp")
def esp_sock(ws):

    global esp

    esp=ws

    print("ESP Connected")

    try:

        while True:

            msg=ws.receive()

            if msg is None:
                break

            print("ESP >",msg)

            if web:
                web.send(msg)

    except:
        pass

    esp=None

    print("ESP Disconnected")


@sock.route("/web")
def web_sock(ws):

    global web

    web=ws

    print("WEB Connected")

    try:

        while True:

            msg=ws.receive()

            if msg is None:
                break

            print("WEB >",msg)

            if esp:
                esp.send(msg)

    except:
        pass

    web=None

    print("WEB Disconnected")


app.run(host="0.0.0.0", port=5000, threaded=True)