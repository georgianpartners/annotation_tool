FROM python:3.7-slim

# Basics
RUN pip install redis pytest Flask Celery seaborn python-dotenv Flask-HTTPAuth flower
RUN pip install mockito flask-sqlalchemy alembic Flask-Testing psycopg2-binary
RUN pip install scikit-learn

# Removed
# simpletransformers supervisor

# Salesforce feedback loop related packages.
RUN pip install google-cloud-secret-manager simple-salesforce PyJWT cryptography requests tldextract

# GCP Cloud logging
RUN pip install google-cloud-logging

# GCP Pub Sub
RUN pip install google-cloud-pubsub

# Spring3 data fetcher
RUN pip install ijson tqdm google-cloud-storage

# Some error with latest pillow verison... downgrade pillow
RUN pip install pillow==6.1

RUN pip install spacy
RUN python -m spacy download en_core_web_sm

# ----------- GOOGLE CLOUD -----------
# Installs google cloud sdk, this is mostly for using gsutil to export model.
RUN apt-get update && apt install wget -y
RUN wget -nv \
    https://dl.google.com/dl/cloudsdk/release/google-cloud-sdk.tar.gz && \
    mkdir /root/tools && \
    tar xvzf google-cloud-sdk.tar.gz -C /root/tools && \
    rm google-cloud-sdk.tar.gz && \
    /root/tools/google-cloud-sdk/install.sh --usage-reporting=false \
        --path-update=false --bash-completion=false \
        --disable-installation-options && \
    rm -rf /root/.config/* && \
    ln -s /root/.config /config && \
    # Remove the backup directory that gcloud creates
    rm -rf /root/tools/google-cloud-sdk/.install/.backup

# Path configuration
ENV PATH $PATH:/root/tools/google-cloud-sdk/bin
