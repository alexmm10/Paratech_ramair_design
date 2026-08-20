FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget software-properties-common python3 python3-pip python3-venv \
    openmpi-bin libopenmpi-dev libglu1-mesa libgl1 && \
    wget -qO /etc/apt/trusted.gpg.d/openfoam.asc https://dl.openfoam.org/gpg.key && \
    add-apt-repository "http://dl.openfoam.org/ubuntu main" && \
    apt-get update && apt-get install -y --no-install-recommends openfoam14 && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    gmsh==4.15.2 numpy pandas matplotlib scipy pillow psutil PyFoam pytest

WORKDIR /workspace
COPY . /opt/ramair-source
ENV PYTHONPATH=/workspace/CFD_2D/scripts
ENTRYPOINT ["bash", "-lc", "source /opt/openfoam14/etc/bashrc && exec \"$@\"", "--"]
CMD ["bash"]
