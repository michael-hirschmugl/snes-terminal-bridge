; =============================================================================
; SNES Terminal — 16×16 tile display test
;
; Displays all 95 printable ASCII characters (0x20–0x7E) on screen using
; 16×16 pixel tiles rendered by tools/gen_font.py.
;
; VRAM layout:
;   $0000–$07FF  BG1 tilemap  (32×32 entries × 2 bytes = 2 KB)
;   $1000–$1BFF  Font tiles   (384 subtiles × 16 bytes = 6 KB)
;
; BG mode: Mode 0, BG1 (16×16 tiles, 2bpp, 4 colours)
; Palette:  colour 0 = black ($0000), colour 1 = white ($7FFF)
; =============================================================================

.setcpu "65816"

; -----------------------------------------------------------------------------
; Hardware registers
; -----------------------------------------------------------------------------

INIDISP  = $2100
BGMODE   = $2105
BG1SC    = $2107
BG12NBA  = $210B
TM       = $212C
CGADD    = $2121
CGDATA   = $2122
VMAIN    = $2115
VMADDL   = $2116
VMADDH   = $2117
VMDATAL  = $2118
VMDATAH  = $2119
NMITIMEN = $4200
DMAP0    = $4300
BBAD0    = $4301
A1TL0    = $4302
A1TH0    = $4303
A1B0     = $4304
DAS0L    = $4305
DAS0H    = $4306
MDMAEN   = $420B

; Tile data constants (must match gen_font.py output)
NUM_GROUPS  = 12          ; ceil(95 / 8)
TOTAL_TILES = NUM_GROUPS * 32   ; = 384 subtiles
FONT_BYTES  = TOTAL_TILES * 16  ; = 6144 bytes

TILEMAP_BYTES = 1024 * 2  ; 32×32 entries × 2 bytes = 2048

; -----------------------------------------------------------------------------
; CODE
; -----------------------------------------------------------------------------

.segment "CODE"

nmi_handler:
irq_handler:
cop_handler:
brk_handler:
abort_handler:
    rti

reset:
    sei
    clc
    xce                         ; → native (65816) mode

    rep     #$30                ; A=16-bit, X=16-bit
    .a16
    .i16
    lda     #$1FFF
    tcs                         ; stack = $1FFF
    lda     #$0000
    tcd                         ; direct page = $0000

    sep     #$20                ; A=8-bit
    .a8

    lda     #$8F                ; force blank
    sta     INIDISP
    stz     NMITIMEN

    ; -------------------------------------------------------------------------
    ; Palette: colour 0 = black, colour 1 = white
    ; -------------------------------------------------------------------------
    stz     CGADD
    stz     CGDATA              ; colour 0 low
    stz     CGDATA              ; colour 0 high  → $0000 = black
    lda     #$FF
    sta     CGDATA              ; colour 1 low
    lda     #$7F
    sta     CGDATA              ; colour 1 high  → $7FFF = white

    ; -------------------------------------------------------------------------
    ; VRAM: increment word address after each VMDATAH write
    ; -------------------------------------------------------------------------
    lda     #$80
    sta     VMAIN

    ; =========================================================================
    ; DMA 1 — tilemap → VRAM word $0000
    ; =========================================================================
    stz     VMADDL
    stz     VMADDH

    lda     #$01                ; DMA mode 1: alternating VMDATAL/VMDATAH
    sta     DMAP0
    lda     #$18                ; B-bus: VMDATAL ($2118)
    sta     BBAD0
    lda     #<tilemap_data
    sta     A1TL0
    lda     #>tilemap_data
    sta     A1TH0
    lda     #^tilemap_data
    sta     A1B0

    rep     #$20
    .a16
    lda     #TILEMAP_BYTES
    sta     DAS0L               ; low and high byte in one 16-bit write
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; DMA 2 — font tiles → VRAM word $1000
    ; =========================================================================
    stz     VMADDL
    lda     #$10
    sta     VMADDH

    ; DMA channel 0 is reused (DMAP0 and BBAD0 unchanged)
    lda     #<font_tiles
    sta     A1TL0
    lda     #>font_tiles
    sta     A1TH0
    lda     #^font_tiles
    sta     A1B0

    rep     #$20
    .a16
    lda     #FONT_BYTES
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; BG configuration
    ; =========================================================================
    lda     #$10                ; Mode 0, BG1 tile size = 16×16 (bit 4)
    sta     BGMODE

    lda     #$00                ; BG1 tilemap at word $0000, 32×32
    sta     BG1SC

    lda     #$01                ; BG1 tile data at word $1000  ($1000/$1000=1)
    sta     BG12NBA

    lda     #$01                ; enable BG1 on main screen
    sta     TM

    lda     #$0F                ; display on, full brightness
    sta     INIDISP

@forever:
    bra     @forever

; -----------------------------------------------------------------------------
; Data (included from generated files)
; -----------------------------------------------------------------------------

.segment "RODATA"

tilemap_data:
.include "../assets/tilemap.inc"

font_tiles:
.include "../assets/font.inc"

; -----------------------------------------------------------------------------
; SNES internal header  ($FFC0–$FFE3)
; -----------------------------------------------------------------------------

.segment "HEADER"
    .byte "SNES TERMINAL       "   ; 21 bytes, space-padded
    .byte $20                      ; $FFD5: SlowROM, LoROM
    .byte $00                      ; $FFD6: ROM only
    .byte $05                      ; $FFD7: 32 KB
    .byte $00                      ; $FFD8: no SRAM
    .byte $01                      ; $FFD9: North America
    .byte $00                      ; $FFDA: developer
    .byte $00                      ; $FFDB: v1.0
    .word $FFFF                    ; $FFDC: complement (test ROM)
    .word $0000                    ; $FFDE: checksum  (test ROM)
    .byte $00, $00, $00, $00       ; $FFE0–$FFE3: padding

; -----------------------------------------------------------------------------
; Interrupt vectors  ($FFE4–$FFFF)
; -----------------------------------------------------------------------------

.segment "VECTORS"
    ; Native mode
    .word cop_handler              ; $FFE4 COP
    .word brk_handler              ; $FFE6 BRK
    .word abort_handler            ; $FFE8 ABORT
    .word nmi_handler              ; $FFEA NMI
    .word $0000                    ; $FFEC unused
    .word irq_handler              ; $FFEE IRQ
    .word $0000                    ; $FFF0 unused
    .word $0000                    ; $FFF2 unused
    ; Emulation mode
    .word cop_handler              ; $FFF4 COP
    .word $0000                    ; $FFF6 unused
    .word abort_handler            ; $FFF8 ABORT
    .word nmi_handler              ; $FFFA NMI
    .word reset                    ; $FFFC RESET ← boot entry point
    .word irq_handler              ; $FFFE IRQ/BRK
