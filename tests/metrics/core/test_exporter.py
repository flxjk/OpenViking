# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


def test_exporter_outputs_help_type_and_empty_metrics(registry, render_prometheus):
    registry.set_counter("openviking_empty_counter_total", 0)
    registry.set_gauge("openviking_empty_gauge", 0)
    registry.set_histogram(
        "openviking_empty_histogram_seconds",
        bucket_bounds=(1,),
        bucket_counts=(0, 0),
        count=0,
        value_sum=0,
    )
    text = render_prometheus(registry)
    assert "# TYPE openviking_empty_counter_total counter" in text
    assert "openviking_empty_counter_total 0.0" in text
    assert "# TYPE openviking_empty_histogram_seconds histogram" in text
    assert 'le="+Inf"} 0.0' in text
