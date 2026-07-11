import os

from waitress import serve

from difflab import create_app

serve(
    create_app(),
    host=os.environ.get("DIFFLAB_HOST", "0.0.0.0"),
    port=int(os.environ.get("DIFFLAB_PORT", "8747")),
)
