#!/usr/bin/python
# Copyright (c) Microsoft Corporation
# All rights reserved.
#
# MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
# to permit persons to whom the Software is furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
# BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import copy
import sys
import time
import logging

import docker_stats
import docker_inspect
import gpu_exporter
import utils
from utils import Metric

logger = logging.getLogger(__name__)


# k8s will prepend "k8s_" to pod name. There will also be a container name prepend with "k8s_POD_"
# which is a docker container used to construct network & pid namespace for specific container. These
# container prepend with "k8s_POD" consume nothing.
pai_services = map(lambda s: "k8s_" + s, [
    "rest-server",
    "pylon",
    "webportal",
    "grafana",
    "prometheus",
    "alertmanager",
    "watchdog",
    "end-to-end-test",
    "frameworklauncher",
    "hadoop-jobhistory-service",
    "hadoop-name-node",
    "hadoop-node-manager",
    "hadoop-resource-manager",
    "hadoop-data-node",
    "zookeeper",
    "node-exporter",
    "gpu-exporter",
    "yarn-exporter",
    "nvidia-drivers"
])

def parse_from_labels(labels):
    gpuIds = []
    otherLabels = {}

    for key, val in labels.items():
        if "container_label_GPU_ID" == key:
            s2 = val.replace("\"", "").split(",")
            for id in s2:
                if id:
                    gpuIds.append(id)
        else:
            otherLabels[key] = val

    return gpuIds, otherLabels


def collect_job_metrics(gpuInfos):
    stats = docker_stats.stats()
    if stats is None:
        logger.warning("docker stats returns None")
        return None

    result = []
    for container_id, stats in stats.items():
        pai_service_name = None

        # TODO speed this up, since this is O(n^2)
        for service_name in pai_services:
            if stats["name"].startswith(service_name):
                pai_service_name = service_name[4:] # remove "k8s_" prefix
                break

        if pai_service_name is None:
            inspectInfo = docker_inspect.inspect(container_id)
            if inspectInfo is None or not inspectInfo["labels"]:
                continue

            gpuIds, otherLabels = parse_from_labels(inspectInfo["labels"])
            otherLabels.update(inspectInfo["env"])

            for id in gpuIds:
                if gpuInfos:
                    logger.info(gpuInfos)
                    labels = copy.deepcopy(otherLabels)
                    labels["minor_number"] = id

                    result.append(Metric("container_GPUPerc", labels, gpuInfos[id]["gpuUtil"]))
                    result.append(Metric("container_GPUMemPerc", labels, gpuInfos[id]["gpuMemUtil"]))

            result.append(Metric("container_CPUPerc", otherLabels, stats["CPUPerc"]))
            result.append(Metric("container_MemUsage", otherLabels, stats["MemUsage_Limit"]["usage"]))
            result.append(Metric("container_MemLimit", otherLabels, stats["MemUsage_Limit"]["limit"]))
            result.append(Metric("container_NetIn", otherLabels, stats["NetIO"]["in"]))
            result.append(Metric("container_NetOut", otherLabels, stats["NetIO"]["out"]))
            result.append(Metric("container_BlockIn", otherLabels, stats["BlockIO"]["in"]))
            result.append(Metric("container_BlockOut", otherLabels, stats["BlockIO"]["out"]))
            result.append(Metric("container_MemPerc", otherLabels, stats["MemPerc"]))
        else:
            labels = {"name": pai_service_name}
            result.append(Metric("service_cpu_percent", labels, stats["CPUPerc"]))
            result.append(Metric("service_mem_usage_byte", labels, stats["MemUsage_Limit"]["usage"]))
            result.append(Metric("service_mem_limit_byte", labels, stats["MemUsage_Limit"]["limit"]))
            result.append(Metric("service_mem_usage_percent", labels, stats["MemPerc"]))
            result.append(Metric("service_net_in_byte", labels, stats["NetIO"]["in"]))
            result.append(Metric("service_net_out_byte", labels, stats["NetIO"]["out"]))
            result.append(Metric("service_block_in_byte", labels, stats["BlockIO"]["in"]))
            result.append(Metric("service_block_out_byte", labels, stats["BlockIO"]["out"]))

    return result

def main(argv):
    log_dir = argv[0]
    gpu_metrics_path = log_dir + "/gpu_exporter.prom"
    job_metrics_path = log_dir + "/job_exporter.prom"
    time_sleep_s = int(argv[1])

    iter = 0

    singleton = utils.Singleton(gpu_exporter.collect_gpu_info)

    while True:
        try:
            logger.info("job exporter running {0} iteration".format(str(iter)))
            iter += 1
            gpu_infos = singleton.try_get()

            gpu_metrics = gpu_exporter.convert_gpu_info_to_metrics(gpu_infos)
            utils.export_metrics_to_file(gpu_metrics_path, gpu_metrics)

            # join with docker stats metrics and docker inspect labels
            job_metrics = collect_job_metrics(gpu_infos)
            utils.export_metrics_to_file(job_metrics_path, job_metrics)
        except Exception as e:
            logger.exception("exception in job exporter loop")

        time.sleep(time_sleep_s)


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(filename)s:%(lineno)s - %(message)s",
            level=logging.INFO)

    main(sys.argv[1:])
