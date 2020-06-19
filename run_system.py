import logging
import json
import os

import pandas as pd
import urllib.request, urllib.parse
from flask import Flask, flash, redirect, render_template, request, url_for
from gevent import pywsgi
from PIL import Image
from scipy.stats import wasserstein_distance
from werkzeug.utils import secure_filename


logging.basicConfig(level=logging.INFO)

# Input stock data path. 
# Assumes format from fetch_data.py : https://github.com/james-kennedy/StockRec
INPUT_DATA = os.path.join(os.getcwd(), "spy_data.json")
LOGO_CACHE = os.path.join(os.getcwd(), "static/logo_cache")
ALLOWED_EXTENSIONS = set(["png", "jpg", "jpeg"])

# Vars for flask web app
HOST = "127.0.0.1"
PORT = 5000
app = Flask(__name__)
app.config["SECRET_KEY"] = "super secret key"
app.config["UPLOAD_FOLDER"] = LOGO_CACHE
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024     # 8mb upload limit


class RecommendationSystem:
    def __init__(self):
        # create cache location folder if it doesn't exist
        if not os.path.isdir(LOGO_CACHE):
            os.makedirs(LOGO_CACHE)

        self.useful_fields = [
            "symbol",
            "shortName",
            "logo_url"
        ]

        all_data = self.load_pandas_df_from_json()
        self.clean_data = all_data[self.useful_fields]      # remove fields we don't care about
        self.clean_data = self.clean_data.dropna()          # remove any rows with missing data

        # download or load logos from cache
        self.clean_data["image"] = self.clean_data.apply(self.download_logos, axis=1)
        self.clean_data = self.clean_data.dropna()          # remove any rows without a logo
        self.clean_data = self.clean_data.reset_index()     # turn index to int base 0
        # make index to ticker map
        self.indices = pd.Series(self.clean_data.index, index=self.clean_data['symbol']).drop_duplicates()

        # calculate RGB data about all logos and store it
        self.clean_data["average_color"] = self.clean_data.apply(self.average_image_color, axis=1)
        
        self.current_image = None
        self.current_histogram = None
        self.current_rgb = None

    def load_pandas_df_from_json(self, json_file_path=INPUT_DATA):
        """
        Load a .json dictionary object with stock data and turn it into a pandas df

        Parameters:
            json_file_path (string):    string to .json file with data

        Return:
            (pandas df)                 json data converted into a df
        """
        with open(json_file_path, "r") as input_file:
            json_data = json.loads(input_file.read())
        assert "stock_data" in json_data
        return pd.DataFrame(json_data["stock_data"]).transpose()

    def download_logos(self, row):
        if row["logo_url"]:
            try:
                # read from cache if possible
                cache_location = os.path.join(LOGO_CACHE, row["symbol"] + ".png")
                if os.path.isfile(cache_location):
                    return Image.open(cache_location)

                # otherwise fetch from web and write to cache
                logo_image = Image.open(urllib.request.urlopen(row["logo_url"].replace("https", "http")))
                logo_image.save(cache_location)
                return logo_image
            except:
                logging.info("Got an error {}".format(row["shortName"]))
                return None
        logging.info("Got an error {}".format(row["shortName"]))
        return None

    def average_image_color(self, row):
        return self.average_color_from_histogram(row["image"].histogram())

    def average_color_from_histogram(self, input_histogram):
        r = input_histogram[0:256]
        g = input_histogram[256:256*2]
        b = input_histogram[256*2: 256*3]

        # calculate the weighted average of each channel
        return [
            sum(i * w for i, w in enumerate(r)) / sum(r),
            sum(i * w for i, w in enumerate(g)) / sum(g),
            sum(i * w for i, w in enumerate(b)) / sum(b)
        ]

    def get_rgb_distance(self, row):
        return wasserstein_distance(row["average_color"], self.current_rgb)

    def get_recommendation(self, input_filepath):
        self.current_image = Image.open(input_filepath)
        self.current_histogram = self.current_image.histogram()
        self.current_rgb = self.average_color_from_histogram(self.current_histogram)

        self.clean_data["rgb_distance"] = self.clean_data.apply(self.get_rgb_distance, axis=1)
        closest_values = self.clean_data.iloc[(self.clean_data["rgb_distance"]).argsort()[1:6]]
        scores = [x for x in closest_values["rgb_distance"]]
        return closest_values[["symbol", "shortName"]], scores


@app.route("/upload", methods=["GET", "POST"])
def upload():
    """
    Modified from https://flask.palletsprojects.com/en/1.1.x/patterns/fileuploads/
    """
    global StockRec

    if request.method == "POST":
        # check if the post request has the file part
        if "logo" not in request.files:
            return render_template(
                "index.html",
                error_msg="No file uploaded"
            ) 
        file = request.files["logo"]
        # if user does not select file, the browser submits no filename
        if file.filename == "":
            return render_template(
                "index.html",
                error_msg="No file selected"
            )
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            full_filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(full_filepath)
            flash("Logo uploaded:")
            recommendations, scores = StockRec.get_recommendation(full_filepath)
            recommendations = recommendations.to_dict("records")
            for rec, score in zip(recommendations, scores):
                rec["score"] = round(score, 2)
            return render_template(
                "index.html",
                filename=filename,
                recs=recommendations,
                cols=["symbol", "shortName", "score"],
                display_cols=["Ticker", "Name", "Score"]
            )
        return redirect(url_for('index'))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/display/<filename>')
def display_image(filename):
	return redirect(url_for('static', filename='logo_cache/' + filename), code=301)


@app.route("/")
def index():
    return render_template(
        "index.html"
    )


def main():
    global StockRec, app
    StockRec = RecommendationSystem()
    logging.info("App launching at http://{}:{}".format(HOST, PORT))
    server = pywsgi.WSGIServer((HOST, PORT), app)
    server.serve_forever() 


if __name__ == "__main__":
    main()
