# View and channel mappings

`ViewMapping` (`waffle.contracts.mappings.mapping`) is an immutable, versioned record
linking one exact `InterfaceDefinition` revision to exact `Layout` and
`PhysicalProblem` revisions. An optional `ModelRevision` reference pins a model.
`validate_mapping` compares all supplied record hashes, verifies that the physical
problem uses the layout's revision, top cell, and grid, and checks a supplied model's
interface revision. A model reference requires its record for validation.

Each `ChannelBinding` corresponds to one logical terminal. Its `layout_port` and
`simulation_port` name ports in their respective views. `anchor` is the expected
center and outward orientation **in the mapping frame**. The mapping's integer
`Transform` places layout and physical ports in that frame; an anchor mismatch
detects a swapped port name even for identical port types and widths. Validation
requires one binding per interface terminal and unique layout, simulation, model,
and present measurement channel assignments. The order of `bindings` is the
declared terminal to model channel permutation. Measurement channels may be absent
when a dataset does not observe a terminal.

The `mode` index is zero based. This version supports only fundamental mode 0 and
the identity modal transform. Other mode indices and declared basis changes return
`mapping.unsupported_mode` or `mapping.unsupported_modal_transform`. A binding
records its signed reference plane offset in layout dbu along the outward port
direction and one normalization convention (`power_wave`, `field_amplitude`, or
`voltage_wave`). These are metadata for subsequent extraction or de-embedding;
validation does not numerically shift a plane or convert normalization.

`ResponseChannel(output, excitation, status)` records scattering response
coverage. `validate_response_coverage(mapping, required_pairs)` reports an absent
or explicitly `uncomputed` element with `mapping.uncomputed_response`; it never
fills missing elements with zero. Omitting `required_pairs` checks the full square
matrix over mapped model channels. `validate_mapping` checks mapping structure
independently, so partial response coverage remains a valid mapping. A caller can
check only the pairs needed for a bounded analysis.

`check_connectivity` checks two mapped terminal ports after each mapping transform.
They must share a database grid size, center, width, signal domain, and logical
layer, and face opposite directions. Different grid sizes receive
`mapping.connectivity.grid_mismatch`; callers must regrid before comparing. Only
orthogonal rotations and reflections represented by `Transform` are supported.
The service also checks the pinned layout revisions and terminal anchors. It does
not infer physical compatibility across distinct technologies or convert modal
bases, amplitudes, or reference planes.
