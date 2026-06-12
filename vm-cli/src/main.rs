//! flux-vm CLI — reads bytecode from stdin, executes, outputs JSON to stdout.
//!
//! Usage:
//!   cat program.bin | flux-vm
//!   MAX_CYCLES=500000 ./flux-vm < program.bin
//!
//! Environment:
//!   MAX_CYCLES   Maximum instruction cycles before budget-exceeded error (default 10_000_000)

use std::io::{self, Read};
use flux_core::vm::Interpreter;

fn main() {
    // ── 1. Read bytecode from stdin ──────────────────────────────────
    let mut bytecode: Vec<u8> = Vec::new();
    if let Err(e) = io::stdin().read_to_end(&mut bytecode) {
        eprintln!("{{}}");  // safe fallback JSON
        eprintln!("error reading stdin: {}", e);
        std::process::exit(1);
    }

    // ── 2. Parse max_cycles from env ─────────────────────────────────
    let max_cycles: u64 = std::env::var("MAX_CYCLES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(10_000_000);

    // ── 3. Execute ───────────────────────────────────────────────────
    let mut vm = Interpreter::new(&bytecode).with_max_cycles(max_cycles);
    let run_result = vm.execute();

    // ── 4. Build JSON output (no external JSON crate) ────────────────
    let success = run_result.is_ok();
    let cycles_used = vm.cycle_count;
    let halted = vm.halted;
    let error_str = match &run_result {
        Ok(_) => String::new(),
        Err(e) => e.to_string(),
    };

    // Escape string for JSON-safe embedding
    fn json_escape(s: &str) -> String {
        let mut out = String::with_capacity(s.len() + 2);
        for ch in s.chars() {
            match ch {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
                c => out.push(c),
            }
        }
        out
    }

    // Build registers array (16 GP i32)
    let regs_json: Vec<String> = vm.regs.gp.iter()
        .map(|v| v.to_string())
        .collect();

    // Build fp_registers array (16 FP f64)
    let fp_json: Vec<String> = vm.regs.fp.iter()
        .map(|v| v.to_string())
        .collect();

    // Build stack array (Vec<i32>)
    let stack_json: Vec<String> = vm.stack.iter()
        .map(|v| v.to_string())
        .collect();

    // Assemble output
    let output = format!(
        "{{\"success\":{},\"cycles_used\":{},\"halted\":{},\"error\":\"{}\",\"pc\":{},\"flag_zero\":{},\"flag_sign\":{},\"registers\":[{}],\"fp_registers\":[{}],\"stack\":[{}]}}",
        if success { "true" } else { "false" },
        cycles_used,
        if halted { "true" } else { "false" },
        json_escape(&error_str),
        vm.regs.pc,
        if vm.regs.flag_zero { "true" } else { "false" },
        if vm.regs.flag_sign { "true" } else { "false" },
        regs_json.join(","),
        fp_json.join(","),
        stack_json.join(","),
    );

    println!("{}", output);
}
