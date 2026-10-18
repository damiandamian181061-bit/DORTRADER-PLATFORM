// The slim Plotly bundle ships no types; this covers the two calls we make.
declare module 'plotly.js-basic-dist-min' {
  type Spec = Record<string, unknown>
  const Plotly: {
    react(
      root: HTMLElement,
      data: Spec[],
      layout?: Spec,
      config?: Spec,
    ): Promise<unknown>
    purge(root: HTMLElement): void
  }
  export default Plotly
}
