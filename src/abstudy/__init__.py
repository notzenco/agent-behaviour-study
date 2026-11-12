"""agent-behaviour-study.

A measurement harness for the question: how often do AI coding agents read,
leak or use credentials they come across, and what affects it?

The package is deliberately split so that the parts which touch the
filesystem, the agent-tripwire CLI or a subprocess can be tested without
running a real agent:

* :mod:`abstudy.config`   study configuration and the trial plan
* :mod:`abstudy.templates` the sandbox task repositories
* :mod:`abstudy.tripwire`  a thin wrapper over the ``tripwire`` CLI
* :mod:`abstudy.agents`    agent runners, including the offline mock agent
* :mod:`abstudy.harness`   per-trial orchestration and isolation
* :mod:`abstudy.results`   tidy result writing and reading
* :mod:`abstudy.analysis`  proportions, confidence intervals and models
* :mod:`abstudy.report`    figures, tables and the generated report
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
