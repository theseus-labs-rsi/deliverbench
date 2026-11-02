<p align="center">
  <a href="https://deliverbench.github.io/"><img src="assets/logo-folio.svg" width="76" alt="DeliverBench project mark"></a>
</p>
<h1 align="center">DeliverBench</h1>
<p align="center"><strong>面向连续、多阶段专业项目交付的 Agent 评测</strong></p>
<p align="center"><a href="https://deliverbench.github.io/"><strong>🌐 项目主页</strong></a> &nbsp; · &nbsp; <a href="https://huggingface.co/datasets/deliverbench/DeliverBench"><strong>🤗 数据集</strong></a> &nbsp; · &nbsp; <a href="#安装与下载"><strong>🚀 快速开始</strong></a> &nbsp; · &nbsp; <a href="https://github.com/theseus-labs-rsi/deliverbench"><strong>🌏 English</strong></a></p>

DeliverBench 以连续的专业项目评估 Agent：同一会话在持久工作区内完成阶段交付，
接收新材料与基于自身产物生成的回应，修订已有工作，最后提交完整成果。

[![从个人辅助、独立任务执行到完整专业项目交付](assets/figure-01-motivation.png)](assets/figure-01-motivation.png)

<sub>评估单位从单个任务扩展到完整的多阶段专业项目。</sub>

## 三个公开案例

| 案例 | 专业工作流 | 过程阶段 | FINAL |
|:---|:---|:---:|:---:|
| **Case 01** | Project Mars 银团贷款再融资 | 13 | 1 |
| **Case 02** | 药物警戒 ICSR 个例处理 | 12 | 1 |
| **Case 03** | StarPulse 法律交付 | 8 | 1 |

首批公开三个完整项目，共 33 个过程阶段和三个独立 FINAL。

## 安装与下载

需要 Python 3.12、uv、Docker 和 Compose，建议为 Docker 至少分配 8 GiB 内存。
镜像从公开软件源构建，需预留镜像和运行产物空间。

```sh
git clone https://github.com/theseus-labs-rsi/deliverbench.git
cd deliverbench
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python uv ./deliverbench-harbor -r requirements-tools.txt
source .venv/bin/activate
python scripts/download_data.py --project all --output-dir data
```

下载固定数据提交并逐文件核验 SHA256。可将 `all` 换为 `case-01`、`case-02` 或 `case-03`。
已有目录只有完全一致时才会复用；存在修改或额外文件时拒绝覆盖。

## 连续执行与评价

[![同一Agent在持久工作区中持续完成任务，分别评价过程交付与最终交付](assets/figure-03-execution.png)](assets/figure-03-execution.png)

<sub>后续任务使用并修订 Agent 自己的已有产物；过程评分与最终交付评分相互独立。</sub>

## 一个完整项目如何衔接

[![银团贷款再融资项目的十三个阶段、产物依赖与六份最终交付物](assets/figure-08-dependencies.png)](assets/figure-08-dependencies.png)

<sub>对应公开案例 Case 01，展示尽调、融资方案、建模审阅到最终签约交付的衔接；沿用论文原图。</sub>

## 无付费检查

```sh
python data/case-01_mars_refinancing/qa/pipeline_probe.py --output "$PWD/checks/case-01"
```

该入口使用参考方案与本地合成 Judge/Responder，不调用付费模型。
输出目录必须是新目录。其他两个项目使用各自的同名入口。合成检查不构成模型成绩。

## 正式运行

按根目录 `.env.example` 在 shell 中设置 Candidate、Judge、Responder 变量；
文件不会自动加载。进入下载的项目目录，按照该项目 README 使用原生 `harbor run`，
结束后用 `deliverbench-check --verify-evidence` 核验。运行与评分调用可能产生费用。
完整命令见[英文主说明](README.md#run-your-model)和[运行说明](docs/RUNNING.md)。

项目开场不评分，材料按阶段释放，后续阶段继承模型自身产物。公开的参考方案、rubric、
后续阶段材料和模拟器内部信息仍与 Candidate 运行环境隔离。评测时不得从公开仓库
另行检索这些隐藏材料。FINAL 独立评分，不平均过程分、不继承历史 hurdle。
缺判决、不可读证据和流程故障不补成零分；真实 hurdle 命中可以产生有效零分。

## 兼容性与许可

公共扩展 0.7.2，原生 Harbor 0.20.0。软件采用 Apache-2.0；
题包内容和[介绍图](assets/README.md)采用 CC BY 4.0，随附可执行脚本采用 Apache-2.0。
三个项目的源材料归属与保留说明见数据集 LICENSING.md。

公开仓库不包含模型成绩、实际作答产物、判决、会话日志、轨迹或运行报告。
用户运行产生的材料保存在自己的本地目录。
