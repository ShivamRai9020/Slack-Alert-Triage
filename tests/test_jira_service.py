from app.jira.service import should_open_incident


def test_high_frequency_spike_always_ticket_worthy():
    assert should_open_incident("high_frequency_spike", None) is True
    assert should_open_incident("high_frequency_spike", "WARNING") is True


def test_requires_investigation_only_ticket_worthy_if_critical():
    assert should_open_incident("requires_investigation", "CRITICAL") is True
    assert should_open_incident("requires_investigation", "WARNING") is False
    assert should_open_incident("requires_investigation", None) is False


def test_noise_and_normal_never_ticket_worthy():
    assert should_open_incident("potential_noise", "CRITICAL") is False
    assert should_open_incident("recurring_normal", "CRITICAL") is False


def test_new_unknown_not_ticket_worthy_by_itself():
    # New alerts get investigated by a human first; only escalate to a
    # ticket once they're confirmed as spiking or critical+unresolved.
    assert should_open_incident("new_unknown", "CRITICAL") is False
