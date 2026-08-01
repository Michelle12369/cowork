import { render, screen } from '@testing-library/react';
import ResultTable from './ResultTable';

test('renders the intent sentence as a caption', () => {
  render(
    <ResultTable
      intent="計算各機台的不良率"
      columns={['machine_id', 'defect_rate']}
      rows={[['M1', 0.02]]}
      truncated={false}
    />,
  );
  expect(screen.getByText('計算各機台的不良率')).toBeInTheDocument();
});

test('renders column headers', () => {
  render(
    <ResultTable
      intent="intent"
      columns={['machine_id', 'defect_rate']}
      rows={[['M1', 0.02]]}
      truncated={false}
    />,
  );
  // antd renders each header label twice (a visible cell plus a hidden zero-height
  // measure row used to compute column widths) — assert at least one visible instance.
  expect(screen.getAllByText('machine_id').length).toBeGreaterThan(0);
  expect(screen.getAllByText('defect_rate').length).toBeGreaterThan(0);
});

test('renders cell values', () => {
  render(
    <ResultTable
      intent="intent"
      columns={['machine_id', 'defect_rate']}
      rows={[
        ['M1', 0.02],
        ['M2', 0.05],
      ]}
      truncated={false}
    />,
  );
  expect(screen.getByText('M1')).toBeInTheDocument();
  expect(screen.getByText('0.02')).toBeInTheDocument();
  expect(screen.getByText('M2')).toBeInTheDocument();
  expect(screen.getByText('0.05')).toBeInTheDocument();
});

test('renders boolean and null cell values as readable text', () => {
  render(
    <ResultTable
      intent="intent"
      columns={['flagged', 'note']}
      rows={[[true, null]]}
      truncated={false}
    />,
  );
  expect(screen.getByText('true')).toBeInTheDocument();
});

test('de-noises IEEE float artifacts without clamping to 3 decimals', () => {
  render(
    <ResultTable intent="intent" columns={['metric']} rows={[[0.1 + 0.2]]} truncated={false} />,
  );
  // 0.1 + 0.2 === 0.30000000000000004 in double precision -- must render as "0.3".
  expect(screen.getByText('0.3')).toBeInTheDocument();
  expect(screen.queryByText('0.30000000000000004')).toBeNull();
});

test('renders a whole-number float without a trailing decimal point', () => {
  render(<ResultTable intent="intent" columns={['total']} rows={[[200.0]]} truncated={false} />);
  expect(screen.getByText('200')).toBeInTheDocument();
  expect(screen.queryByText('200.0')).toBeNull();
});

test('does not alter an already-short decimal value', () => {
  render(<ResultTable intent="intent" columns={['rate']} rows={[[0.02]]} truncated={false} />);
  expect(screen.getByText('0.02')).toBeInTheDocument();
});

test('preserves a user-specified 5-decimal value instead of clamping it to 3', () => {
  // Backend methods like group_compare(decimals=5) produce wire-correct 5-decimal values;
  // this surface must not silently clamp them to 3.
  render(
    <ResultTable
      intent="intent"
      columns={['rate_a', 'rate_b']}
      rows={[[0.01254, 0.052]]}
      truncated={false}
    />,
  );
  expect(screen.getByText('0.01254')).toBeInTheDocument();
  expect(screen.getByText('0.052')).toBeInTheDocument();
});

test('de-noises a longer non-terminating float without clamping to 3 decimals', () => {
  render(
    <ResultTable
      intent="intent"
      columns={['value']}
      rows={[[15.811388300841896]]}
      truncated={false}
    />,
  );
  expect(screen.getByText('15.8113883008')).toBeInTheDocument();
  expect(screen.queryByText('15.811')).toBeNull();
});

test('de-noises a large-magnitude float noise artifact matching the live 總營收 regression', () => {
  // Python sum() over 220 floats produced 267602.25000000006; noise sits past a fixed
  // 10-decimal cutoff on a 6-digit-integer value, so 12-significant-digit shortening is needed.
  render(
    <ResultTable
      intent="intent"
      columns={['總營收']}
      rows={[[267602.25000000006]]}
      truncated={false}
    />,
  );
  expect(screen.getByText('267602.25')).toBeInTheDocument();
  expect(screen.queryByText('267602.2500000001')).toBeNull();
  expect(screen.queryByText('267602.25000000006')).toBeNull();
});

test('shortens extreme-magnitude floats without exponent notation', () => {
  // toPrecision(12) can fall back to exponent notation for very large/small magnitudes;
  // this surface always re-expands to plain fixed notation for a user-facing table cell.
  render(
    <ResultTable
      intent="intent"
      columns={['huge', 'tiny']}
      rows={[[1500000000000.5, 0.000001]]}
      truncated={false}
    />,
  );
  expect(screen.getByText('1500000000000')).toBeInTheDocument();
  expect(screen.getByText('0.000001')).toBeInTheDocument();
});

test('shows the truncated hint when truncated is true', () => {
  render(<ResultTable intent="intent" columns={['a']} rows={[[1]]} truncated={true} />);
  expect(screen.getByText(/前 200 列/)).toBeInTheDocument();
});

test('does NOT show the truncated hint when truncated is false', () => {
  render(<ResultTable intent="intent" columns={['a']} rows={[[1]]} truncated={false} />);
  expect(screen.queryByText(/前 200 列/)).toBeNull();
});
